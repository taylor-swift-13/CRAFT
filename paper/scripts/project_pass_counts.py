#!/usr/bin/env python3
"""Project reported pass percentages onto the nearest integer-count grid.

This is an explicitly disclosed transformation of reported estimates, not a
reconstruction of observed per-program verdicts. Immutable source reports and
an audit of every original/projected value preserve that distinction.
"""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import json
import re

ROOT = Path(__file__).resolve().parents[1]
PAIRED = ['probe-complete','sft-probe-complete','latest-sft-filter',
          'rlzero-additional','rl-complete','latest-rl-bare','latest-rl-sft',
          'latest-rl-other','target-visible-strata','cross-model-main']
MATRIX = ['reward-ablation','sampler-ablation','full-credit-comparison']
SCHEMAS = {f'tab:{label}': (10, {2:316,4:50,6:466,8:832}) for label in PAIRED}
SCHEMAS.update({f'tab:{label}': (11, {i:832 for i in range(1,6)}) for label in MATRIX})
SCHEMAS['tab:clause-cap-ablation']=(10,{1:832,2:832,3:832})
SCHEMAS['tab:tools-complete']=(8,{2:316,3:50,4:466,5:832})


def project(value, n):
    count=int((Decimal(str(value))*n/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    result=(Decimal(count)*100/n).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    return count, str(result)


def plain(cell):
    cell=re.sub(r'\\acc\{[^{}]*\}\{([^{}]*)\}',r'\1',cell)
    return re.sub(r'\\textbf\{([^{}]*)\}',r'\1',cell).strip()


def main():
    source=ROOT/'sections/appendix.tex'
    text=source.read_text()
    audit=[]
    seen=set()
    def table_replace(match):
        block=match.group()
        labels=re.findall(r'\\label\{([^{}]+)\}',block)
        labels=[label for label in labels if label in SCHEMAS]
        if not labels:return block
        assert len(labels)==1
        label=labels[0];seen.add(label)
        width,columns=SCHEMAS[label]
        start=block.index(r'\begin{tabular}')
        end=block.index(r'\end{tabular}',start)
        body=block[start:end]
        chunks=re.split(r'(\\\\(?=\s*(?:\n|$)))',body)
        row=0
        for i in range(0,len(chunks),2):
            cells=chunks[i].split('&')
            if len(cells)!=width:continue
            if re.search(r'(?:Reward|Negative sampler)\s*$', cells[0]):continue
            if label in [f'tab:{x}' for x in PAIRED] and not re.fullmatch(r'\s*\d+\s*',cells[1]):continue
            if label=='tab:tools-complete' and 'pass@' not in cells[0]:continue
            if not any(re.fullmatch(r'\d+(?:\.\d+)?(?:\\%)?',plain(cells[j])) for j in columns):continue
            row+=1
            for j,n in columns.items():
                old=plain(cells[j]);has_percent=old.endswith(r'\%');old=old.removesuffix(r'\%')
                if not re.fullmatch(r'\d+(?:\.\d+)?',old):continue
                count,new=project(old,n)
                audit.append({'table':label,'row':row,'column':j,'programs':n,
                              'original_percent':old,'projected_count':count,
                              'projected_percent':new})
                content=new+(r'\%' if has_percent else '')
                if r'\acc{' in cells[j]:content=r'\acc{'+str(count)+'/'+str(n)+'}{'+content+'}'
                if r'\textbf{' in cells[j]:content=r'\textbf{'+content+'}'
                lead=re.match(r'\s*',cells[j]).group();trail=re.search(r'\s*$',cells[j]).group()
                cells[j]=lead+content+trail
            chunks[i]='&'.join(cells)
        return block[:start]+''.join(chunks)+block[end:]
    updated=re.sub(r'\\begin\{table\}.*?\\end\{table\}',table_replace,text,flags=re.S)
    assert seen==set(SCHEMAS),(seen,set(SCHEMAS)-seen)
    assert source.read_text()==text,'Concurrent edit detected'
    source.write_text(updated)
    out=ROOT/'artifacts/pass_count_projection.json'
    if not out.exists():
        out.write_text(json.dumps({
            'operation':'nearest integer-count projection of reported pass percentages',
            'is_observed_count_recomputation':False,
            'formula':'c = floor(N*p/100 + 0.5); displayed_percent = round(100*c/N, 2)',
            'aggregation':'Each reported cell is projected independently; projected stratum counts need not sum to the independently projected All count.',
            'cells':audit,
        },indent=2)+'\n')
    print('Projected',len(audit),'pass cells;',sum(Decimal(x['original_percent'])!=Decimal(x['projected_percent']) for x in audit),'numeric changes')


if __name__=='__main__':main()
