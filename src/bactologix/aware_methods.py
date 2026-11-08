"""Methods prose from checksummed stage receipts, without inspecting read files."""
import csv
import hashlib
import io
import json


def describe(run, complete, read):
    if run['tool'] != 'hybas':
        return None
    files = []
    aware = run.get('aware', {})
    identification = run.get('identification', {})
    def verified(relative, expected, schema):
        raw = read(run['output'], relative)
        if raw is None or not expected or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Saved AWARE stage evidence changed or is missing; Methods export is blocked.')
        files.append(dict(path=relative,sha256=expected,bytes=len(raw)))
        doc = json.loads(raw)
        if doc.get('schema') != schema or doc.get('isolate') != run['sample']['id']:
            raise ValueError('AWARE Methods evidence belongs to another sample or schema.')
        return doc
    if aware.get('bundle_sha256'):
        relative = 'reports/aware/manifest.json'
        doc = verified(relative, aware['bundle_sha256'], 'hybas-aware-bundle-v1')
        raw = read(run['output'], 'reports/aware/filtering.tsv')
        expected = next((f for f in doc['files'] if f['path']=='filtering.tsv'), None)
        if raw is None or not expected or hashlib.sha256(raw).hexdigest()!=expected['sha256']:
            raise ValueError('Recorded filtering decisions changed; Methods export is blocked.')
        files.append(dict(path='reports/aware/filtering.tsv',sha256=expected['sha256'],bytes=len(raw)))
        filtering = dict((r['field'],r['value']) for r in csv.DictReader(io.StringIO(raw.decode()), delimiter='\t'))
        if filtering.get('isolate') != run['sample']['id']:
            raise ValueError('Filtering evidence belongs to another sample.')
        primary = next(g for g in doc['groups'] if g['id']=='primary')
        target=filtering.get('selected_target_taxid') or 'not recorded'
        mode=filtering.get('kraken_decision_mode') or 'not recorded'
        paragraphs=[f'Kraken2 classification was followed by AWARE target handling in {mode} mode. The recorded selected target was TaxID {target}.']
        for platform, units in primary['units'].items():
            action=filtering.get(platform+'_kraken_decision','not recorded')
            reason=filtering.get(platform+'_kraken_decision_reason','not recorded')
            paragraphs.append(f'For {platform}, the recorded cleaning action was {action} ({reason}); the primary output retained {units} '+('read pairs.' if platform=='illumina' else 'reads.'))
        preserved=any(g['id']!='primary' for g in doc['groups'])
        if preserved:
            paragraphs.append('Extended read preservation retained provisional species assignments, shared ambiguous reads and unclassified reads from the original inputs. Each read or pair was stored once within that partition. The separate primary output can overlap those groups; groups are not independent biological replicates or verified strain reconstructions.')
        stats=doc.get('statistics')
        if stats:
            paragraphs.append('Assignment support was summarized using 95% Wilson intervals separately by platform and library/read group. The sampling unit was one Illumina read pair or one long read. Intervals assume independent sampling units and do not estimate the probability of correct identification.')
            stability=stats.get('stability')
            if stability:
                paragraphs.append(f'Selection stability was evaluated using {stability["replicates"]} stratified multinomial bootstrap replicates of original Kraken assignments (seed {stability["seed"]}), applying the existing automatic target-election rule. The statistical target was TaxID {stats["target_taxid"]}, representing the automatic recommendation before any manual override. Database bias and read dependence were not corrected.')
        if not complete:
            paragraphs.append('This export covers the completed AWARE stage. Completion of assembly, polishing and downstream annotation has not been established.')
        return dict(stage='assembly' if complete else 'aware', state='Completed workflow' if complete else 'AWARE complete; assembly not established',
            paragraphs=paragraphs, tools=['kraken2','snakemake'], evidence=dict(bundle_sha256=aware['bundle_sha256'],filtering=filtering,statistics=stats,preserved_groups=preserved),
            origin='Checksummed AWARE stage evidence',files=files)
    if not complete and identification.get('evidence_sha256'):
        doc=verified('reports/identification/review.json',identification['evidence_sha256'],'hybas-identification-review-v1')
        recommendation=doc.get('recommendation') or {}
        return dict(stage='identification',state='Identification complete; filtering not established',tools=['kraken2','snakemake'],
            paragraphs=['Kraken2 identification completed and its report was retained for researcher review. The automatic recommendation was TaxID '+str(recommendation.get('taxid') or 'not recorded')+'. Filtering and assembly completion have not been established; no downstream methods are claimed in this export.'],
            evidence=dict(review_sha256=identification['evidence_sha256'],recommendation=recommendation),origin='Checksummed identification-stage evidence',files=files)
    return None
