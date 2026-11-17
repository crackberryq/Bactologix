"""Small durable workspace. SQLite transactions coordinate the UI and worker."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from platformdirs import user_data_dir
from .storage_guard import activity, exclusive, mutation

CONTROL_TIMEOUT = .05
CONTROL_BACKOFF = (.05, .1, .2)
CONTROL_ITEMS = {'disk_safety', 'disk_safety_requests', 'job_telemetry', 'job_usage_history',
                 'desktop_sessions', 'desktop_browser', 'job_owners', 'processes'}


def transient_lock(exc):
    code = getattr(exc, 'sqlite_errorcode', None)
    if code is not None:
        return (code & 0xff) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_PROTOCOL}
    # Exact fallback also supports Python/SQLite builds without error-code metadata.
    return str(exc).lower() in {'database is locked', 'database table is locked', 'locking protocol'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def workspace(path=None):
    from .channels import current, default_workspace
    key = "BACTOLOGIX_DEV_HOME" if current() == "dev" else "BACTOLOGIX_HOME"
    return Path(path or os.environ.get(key) or default_workspace()).expanduser().resolve()


def identifier(prefix):
    return prefix + "-" + uuid.uuid4().hex[:12]


class Store:
    def __init__(self, home=None):
        self.home = workspace(home)
        from .channels import claim
        self.channel = claim(self.home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("logs", "runs", "sources", "controllers", "envs", "resources"):
            from .channels import owned
            owned(self, self.home / name, name)
            (self.home / name).mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                  kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                  PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                  status TEXT NOT NULL, created TEXT NOT NULL, started TEXT,
                  finished TEXT, error TEXT, cancel INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS job_queue (
                  job_id TEXT PRIMARY KEY, position INTEGER NOT NULL,
                  held INTEGER NOT NULL DEFAULT 0 CHECK(held IN (0,1)));
            """)

    @contextmanager
    def connect(self, *, timeout=30):
        db = sqlite3.connect(self.home / "workspace.sqlite3", timeout=timeout)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def control_read(self, reader, on_retry=None):
        """Retry a complete read transaction, never a partially committed write.

        Four short attempts keep cancellation and disk checks within their normal
        polling budget; the ordinary 30-second database timeout is unsuitable here.
        """
        from .supervision import MonitoringInterrupted
        for attempt in range(len(CONTROL_BACKOFF) + 1):
            try:
                with self.connect(timeout=CONTROL_TIMEOUT) as db:
                    db.execute('BEGIN')
                    return reader(db)
            except sqlite3.Error as exc:
                retry = transient_lock(exc) and attempt < len(CONTROL_BACKOFF)
                if on_retry is not None:
                    on_retry(exc, attempt + 1)
                if not retry:
                    raise MonitoringInterrupted('Execution monitoring lost access to workspace control data: ' + str(exc)) from exc
                time.sleep(CONTROL_BACKOFF[attempt])
            except (ValueError, TypeError) as exc:
                raise MonitoringInterrupted('Workspace control metadata could not be read: ' + str(exc)) from exc

    def control_state(self, jid, on_retry=None):
        def read(db):
            job = db.execute('SELECT kind,status,cancel FROM jobs WHERE id=?', (jid,)).fetchone()
            if job is None:
                from .supervision import MonitoringInterrupted
                raise MonitoringInterrupted('Execution monitoring cannot find its saved job.')
            if job['kind'] == 'app_update':
                return dict(job, owner=None, session=None)
            row = db.execute("SELECT body FROM items WHERE kind='job_owners' AND id=?", (jid,)).fetchone()
            owner = json.loads(row[0]) if row else None
            if row and (not isinstance(owner, str) or not owner):
                raise ValueError('Invalid desktop owner identity')
            row = db.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?", (owner,)).fetchone() if owner else None
            session = json.loads(row[0]) if row else None
            if row and not isinstance(session, dict):
                raise ValueError('Invalid desktop session metadata')
            return dict(job, owner=owner, session=session)
        return self.control_read(read, on_retry)

    def put_process(self, jid, value):
        """Bounded control bookkeeping; never delay the disk watchdog for 30s."""
        with self.connect(timeout=CONTROL_TIMEOUT) as db:
            db.execute("INSERT INTO items VALUES ('processes',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body", (jid, json.dumps(value)))

    def put(self, kind, key, value):
        guarded = kind in {'samples', 'runs', 'installs', 'configs', 'database_locations',
                           'database_downloads', 'database_builds', 'reference_packages',
                           'reports', 'report_refreshes', 'run_visibility'} or kind == 'settings'
        if guarded:
            with activity(self):
                return self._put(kind, key, value)
        return self._put(kind, key, value)

    def _put(self, kind, key, value):
        if self.channel == 'dev':
            from .channels import owned
            if kind == 'installs':
                for field in ('source', 'controller', 'rule_cache'):
                    if value.get(field): owned(self, value[field], field)
            if kind == 'settings' and key == 'conda' and value:
                if Path(value).resolve() != (self.home / 'tools/miniforge/bin/conda').resolve():
                    raise ValueError('Dev uses only its dedicated Conda installation.')
        with self.connect(timeout=CONTROL_TIMEOUT if kind in CONTROL_ITEMS else 30) as db:
            db.execute("INSERT INTO items VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                       (kind, key, json.dumps(value)))
        return value

    def get(self, kind, key, default=None):
        if kind in CONTROL_ITEMS:
            def read(db):
                row = db.execute("SELECT body FROM items WHERE kind=? AND id=?", (kind, key)).fetchone()
                return json.loads(row[0]) if row else default
            return self.control_read(read)
        with self.connect() as db:
            row = db.execute("SELECT body FROM items WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, kind):
        with self.connect() as db:
            rows = db.execute("SELECT body FROM items WHERE kind=? ORDER BY rowid DESC", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    @mutation
    def register_run(self, run):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT body FROM items WHERE kind='installs' AND id=?", (run['installation'],)).fetchone()
            installed = json.loads(row[0]) if row else {}
            if not installed.get('ready') or installed.get('version', {}).get('commit') != run['commit']:
                raise ValueError('The installation changed or was removed while creating this draft. Refresh and try again.')
            db.execute("INSERT INTO items VALUES ('runs',?,?)", (run['id'], json.dumps(run)))

    @mutation
    def activate_installation(self, key):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT body FROM items WHERE kind='installs' AND id=?", (key,)).fetchone()
            item = json.loads(row[0]) if row else {}
            if not item.get('ready'):
                raise ValueError('This installation is no longer ready. Refresh the installation list.')
            db.execute("INSERT INTO items VALUES ('settings',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                       ('active-' + item['tool'], json.dumps(key)))

    def submit(self, kind, payload):
        with (exclusive(self) if kind in {'storage_cleanup', 'factory_reset'} else activity(self)):
            return self._submit(kind, payload)

    def _submit(self, kind, payload):
        jid = identifier("job")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = os.environ.get('BACTOLOGIX_DESKTOP_SESSION')
            if owner:
                import time
                session = db.execute("SELECT body FROM items WHERE kind='desktop_sessions' AND id=?", (owner,)).fetchone()
                state = json.loads(session[0]) if session else {}
                if state.get('phase') != 'ready' or time.time() - state.get('last_seen', 0) > 30:
                    raise ValueError('This desktop session is closing or disconnected. Reopen Bactologix before starting work.')
            if kind in {'check', 'environments', 'database_download', 'database_redownload'} and payload.get('installation'):
                if not db.execute("SELECT 1 FROM items WHERE kind='installs' AND id=?", (payload['installation'],)).fetchone():
                    raise ValueError('This installation was removed. Refresh before starting maintenance.')
            if kind == 'workflow':
                from .variants import lock_active
                if lock_active(db, payload.get('run_id')):
                    raise ValueError('A variant is copying this run. Wait for the snapshot to finish.')
            pending = db.execute("SELECT kind FROM jobs WHERE status IN ('queued','running')").fetchall()
            if kind == 'isolate_export' and pending:
                raise ValueError('Finish or cancel queued/running jobs before exporting an isolate snapshot.')
            if any(row[0] == 'isolate_export' for row in pending):
                raise ValueError('An isolate export is queued or running. Finish or cancel it before starting other work.')
            if kind in {'storage_cleanup', 'factory_reset'} and pending:
                raise ValueError('Finish or cancel queued/running jobs before deleting stored data.')
            if any(row[0] == "app_update" for row in pending):
                raise ValueError("A Bactologix update is queued or running. Wait for it to finish and restart the app.")
            if kind == "app_update":
                from .queue import control
                if control(db)['paused']:
                    raise ValueError('Resume the queue before installing a Bactologix update.')
            if kind == "app_update" and pending:
                raise ValueError("Finish or cancel queued/running jobs before updating Bactologix.")
            if kind == 'workstation' and any(row[0] == 'workstation' for row in pending):
                raise ValueError('A workstation check is already queued or running.')
            if kind == "workflow":
                visible = db.execute("SELECT body FROM items WHERE kind='run_visibility' AND id=?", (payload["run_id"],)).fetchone()
                if visible and json.loads(visible[0]).get("archived"):
                    raise ValueError("Restore this run from Archived before starting a job.")
                active = db.execute("SELECT payload FROM jobs WHERE kind='workflow' AND status IN ('queued','running')").fetchall()
                if any(json.loads(row[0]).get("run_id") == payload["run_id"] for row in active):
                    raise ValueError("This run already has a queued or active job")
            if kind in {"database_download", "database_redownload"}:
                active = db.execute("SELECT payload FROM jobs WHERE kind IN ('database_download','database_redownload') AND status IN ('queued','running')").fetchall()
                if any(json.loads(row[0]).get("id") == payload["id"] or
                       (json.loads(row[0]).get('tool'), json.loads(row[0]).get('resource')) ==
                       (payload.get('tool'), payload.get('resource')) for row in active):
                    raise ValueError("This database download is already queued or running")
            db.execute("INSERT INTO jobs(id,kind,payload,status,created) VALUES (?,?,?,'queued',?)",
                       (jid, kind, json.dumps(payload), now()))
            if owner:
                db.execute("INSERT INTO items VALUES ('job_owners',?,?)", (jid, json.dumps(owner)))
            position = max(db.execute('SELECT COALESCE(MAX(position),0) FROM job_queue').fetchone()[0],
                           db.execute('SELECT MAX(rowid) FROM jobs').fetchone()[0]) + 1
            db.execute('INSERT INTO job_queue VALUES (?,?,0)', (jid, position))
        return jid

    def jobs(self):
        with self.connect() as db:
            rows = db.execute("SELECT j.*,COALESCE(q.held,0) AS held,COALESCE(q.position,j.rowid) AS queue_position FROM jobs j LEFT JOIN job_queue q ON q.job_id=j.id ORDER BY j.rowid DESC").fetchall()
        return [dict(row, payload=json.loads(row["payload"])) for row in rows]

    def job(self, jid):
        with self.connect() as db:
            row = db.execute("SELECT j.*,COALESCE(q.held,0) AS held,COALESCE(q.position,j.rowid) AS queue_position FROM jobs j LEFT JOIN job_queue q ON q.job_id=j.id WHERE j.id=?", (jid,)).fetchone()
        return dict(row, payload=json.loads(row["payload"])) if row else None

    def update_job(self, jid, **fields):
        if not fields or not set(fields) <= {"status", "started", "finished", "error", "cancel"}:
            raise ValueError("Invalid job update")
        with self.connect() as db:
            db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                       (*fields.values(), jid))

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            from .queue import control
            paused = control(db)['paused']
            row = db.execute("SELECT j.id FROM jobs j LEFT JOIN job_queue q ON q.job_id=j.id "
                             "WHERE j.status='queued' AND j.cancel=0 AND COALESCE(q.held,0)=0 "
                             "AND (?=0 OR j.kind='workflow_unlock') "
                             "ORDER BY COALESCE(q.position,j.rowid),j.rowid LIMIT 1", (int(paused),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (now(), row[0]))
        return self.job(row[0])

    def queue_snapshot(self):
        from .queue import snapshot
        with self.connect() as db:
            db.execute('BEGIN')
            return snapshot(db)

    def cancel(self, jid):
        # This mutation is idempotent. Retry its entire short transaction only
        # after connect() has rolled back and closed the failed attempt. Re-read
        # status each time: a queued application update may now be installing.
        for attempt in range(len(CONTROL_BACKOFF) + 1):
            try:
                with self.connect(timeout=CONTROL_TIMEOUT) as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT kind,status FROM jobs WHERE id=?", (jid,)).fetchone()
                    if row and row[0] == "app_update" and row[1] == "running":
                        raise ValueError("An application update cannot be cancelled while it is installing. Wait for its result.")
                    db.execute("UPDATE jobs SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=? AND status IN ('queued','running')", (jid,))
                return
            except sqlite3.Error as exc:
                if not transient_lock(exc) or attempt >= len(CONTROL_BACKOFF):
                    raise
                time.sleep(CONTROL_BACKOFF[attempt])

    def log_path(self, jid):
        if not jid.startswith("job-") or not jid[4:].isalnum():
            raise ValueError("Invalid job ID")
        return self.home / "logs" / (jid + ".log")

