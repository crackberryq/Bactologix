# Notifications

The header bell opens a workspace-specific notification center. It keeps the latest 200 alerts for job outcomes, runs awaiting identification review and available updates. Mark one or all alerts as read. Muting a category suppresses its unread/desktop alerts while preserving the recorded history.

Validation success, completed workflow execution and a workflow paused for review are different events. An alert reports software status; it does not certify the scientific completeness or safety of a result. Open the run or job log to review its evidence.

## Desktop alerts

Select **Enable desktop alerts** to request browser permission. This preference is local to this browser and workspace. It does not enable email, mobile push or a cloud notification service. Existing notifications are not replayed when desktop alerts are first enabled. Generic popup text does not include sample names, input paths or raw errors.

The browser must support notifications in a secure context, normally localhost or HTTPS. Denied permission, unsupported app windows, OS notification settings and Do Not Disturb may prevent a popup. The in-app center remains available. If needed, use **Restart as browser** and enable alerts there. Browser tests simulate the notification interface; physical Windows notification delivery must be checked on the workstation.

Keep Bactologix open or minimized for desktop alerts and update checks. No background push service is installed for a closed application. Browser suspension can delay delivery. On return, saved job outcomes are collected into the center. Desktop attempts are claimed once per workspace alert to avoid duplicate popups from multiple tabs. An interrupted/failed browser delivery does not mark the in-app alert as read.

## Update availability

The app checks in the background on startup when due and periodically while open. The default interval is 12 hours. **Check for updates** requests a fresh check. Network failures are shown with the last successful check; a failure is not reported as proof that software is up to date.

Application updates must match the current regular/Dev channel and be newer than the running/installed version. Pipeline alerts require a reviewed compatible release or development snapshot and evidence that it is ahead of the active installed commit. Unknown or divergent source relationships are not labelled updates. Checks do not install software or change existing runs, environments or configurations.

Automatic checks can be disabled in the notification center. For offline deployments and automated tests, set `BACTOLOGIX_UPDATE_CHECKS=0` before launching Bactologix. Local job/review notifications continue to work.
