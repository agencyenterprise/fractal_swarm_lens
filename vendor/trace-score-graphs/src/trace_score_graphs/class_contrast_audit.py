"""Independently recompute all saved effect sizes with SciPy U statistics."""
import json
from pathlib import Path
import numpy as np
from scipy.stats import mannwhitneyu
from trace_score_graphs.common import file_sha, save


def main():
    root=Path.cwd()
    for entry in json.loads((root/'class-contrast-report.json').read_text())['runs']:
        p=root/entry['directory']
        source=json.loads((p/'source-hashes.json').read_text())
        for relative,expected in source.items():
            assert file_sha(root/relative)==expected,relative
        idx=json.loads((p/'measurement-index.json').read_text())
        data=np.load(p/'measurements.npz');x=data['conversations']
        y=np.array([r['labels'] for r in idx['conversations']]);fw=np.array([r['framework'] for r in idx['conversations']]);events=np.array([r['events'] for r in idx['conversations']])
        length=np.digitize(events,[10,20,50,100]);ss=np.array([f'{f}|length-bin-{b}' for f,b in zip(fw,length)])
        clean=np.all(y==0,axis=1);problem=np.any(y==1,axis=1)
        labels=['1.1','1.2','1.3','1.4','1.5','2.1','2.2','2.3','2.4','2.5','2.6','3.1','3.2','3.3']
        a=json.loads((p/'analysis.json').read_text());verified=0
        def delta(values, pos, neg):
            xp=np.round(values[pos & np.isfinite(values)],10);xn=np.round(values[neg & np.isfinite(values)],10)
            return 2*mannwhitneyu(xp,xn,method='asymptotic').statistic/(len(xp)*len(xn))-1 if len(xp) and len(xn) else np.nan
        for r in a['contrasts']:
            vals=x[:,idx['graphs'].index(r['graph']),idx['metrics'].index(r['metric'])]
            if r['class']=='any_problem':pos,neg=problem,clean
            else:
                j=labels.index(r['class']);pos=y[:,j]==1
                neg=clean if r['reference']=='no_issue' else (y[:,j]==0)&problem if r['reference']=='other_problems' else y[:,j]==0
            expected=delta(vals,pos,neg)
            assert np.isclose(expected,r['pooled_delta'] if r['pooled_delta'] is not None else np.nan,atol=1e-10,equal_nan=True)
            for strata,key in ((fw,'adjusted_delta'),(ss,'length_adjusted_delta')):
                ds=[delta(vals,pos&(strata==f),neg&(strata==f)) for f in set(strata)]
                ds=[v for v in ds if np.isfinite(v)]
                expected=np.mean(ds) if ds else np.nan
                assert np.isclose(expected,r[key] if r[key] is not None else np.nan,atol=1e-10,equal_nan=True),(r,key,expected)
            verified+=1
        for i,meta in enumerate(idx['conversations']):
            if meta['windows']==1:assert np.isnan(x[i,:,idx['metrics'].index('aligned_matrix_drift')]).all()
        result={'passed':True,'contrasts_recalculated_with_scipy_U':verified,'source_hashes_verified':len(source),'single_window_drift_missingness_verified':True}
        save(p/'independent-audit.json',result)
        print(p.name,result,flush=True)


if __name__=="__main__":
    main()
