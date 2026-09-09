"""Independent expected display contract; no production imports."""
import json,re,sys

def check(case, observed):
    kind=case['kind']
    if kind=='partition':
        assert observed['main']==case['main'],observed
        assert observed['appendix']==case['appendix'],observed
        assert '12 mV' in observed['reviewable_text'],observed
        assert observed['raw']==1 and observed['reader_safe'],observed
    elif kind=='similar':
        assert observed['old_regions']==[1] and observed['new_regions']==[1],observed
    elif kind=='short':
        assert observed['images']>=2,observed
        assert observed['full_pages'] and observed['highlighted'],observed
        assert observed['image_before_text'] and observed['folded'],observed
    elif kind=='table':
        assert observed['tables']>=1,observed
        assert observed['context_bbox']==[0.,0.,300.,400.],observed
        assert observed['detector_bbox']!=observed['context_bbox'],observed
        assert observed['context_image'] and observed['context_in_html'],observed
    elif kind=='invalid':
        assert observed['images']==0 and observed['warnings'],observed
    else: raise AssertionError(kind)

def main():
    samples=[({'kind':'partition','main':0,'appendix':1},dict(main=0,appendix=1,raw=1,reviewable_text='12 mV',reader_safe=True)),
             ({'kind':'short'},dict(images=2,full_pages=True,highlighted=True,image_before_text=True,folded=True)),
             ({'kind':'table'},dict(tables=1,context_bbox=[0.,0.,300.,400.],detector_bbox=[20,100,150,150],context_image=True,context_in_html=True)),
             ({'kind':'invalid'},dict(images=0,warnings=['source mismatch']))]
    for case,data in samples:
        check(case,data)
        key={'partition':'raw','short':'full_pages','table':'context_image','invalid':'warnings'}[case['kind']]
        bad=dict(data);bad[key]=0
        try:check(case,bad)
        except AssertionError:pass
        else:raise AssertionError('negative control survived')
    for p in sys.argv[1:]:
        for c in json.load(open(p))['cases']:assert c['kind'] in {'partition','short','table','invalid','similar'}
    print('SCREENSHOT_ORACLE_OK')
if __name__=='__main__': main()
