"""Build small controlled layout inputs; real document gold remains separate."""
import argparse
import json
from pathlib import Path
import fitz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    cases = []
    texts = {
        'native':['Equipment requirements','Mode A: limit = +3.0 V','Mode B: limit = -3.0 V','The interface shall not enable remote access.'],
        'cjk':['通用设备要求','工作模式 A：电压限制为 3.0 V','工作模式 B：电压限制为 2.5 V','设备不得开启远程访问。'],
        'columns':['LEFT COLUMN','Input signal follows the declared limits.','The filter preserves the source waveform.','Changes require a documented verification.',
                   'RIGHT COLUMN','Output signal follows the measured limits.','The receiver retains every measurement.','Results require independent confirmation.'],
    }
    texts['columns_interleaved'] = list(texts['columns'])
    for name, lines in texts.items():
        with fitz.open() as document:
            page = document.new_page(width=612, height=792)
            order = [0,4,1,5,2,6,3,7] if name == 'columns_interleaved' else range(len(lines))
            for i in order:
                line = lines[i]
                if name.startswith('columns'):
                    point, size = (45 if i < 4 else 325, 80+30*(i%4)), 9
                else:
                    point, size = (60,80+35*i), 12
                page.insert_text(point,line,fontsize=size,fontname='china-s' if name=='cjk' else 'helv')
            path = root/(name+'.pdf'); document.save(path)
        cases.append(dict(id=name,path=str(path),pages=[1],expect={'pages':[dict(page=1,contains=lines,ordered=lines)]}))
    with fitz.open() as document:
        page = document.new_page(width=612,height=792)
        page.insert_text((60,60),'Figure 1: Signal path',fontsize=12)
        for x,label in ((100,'BLOCK A'),(250,'BLOCK B')):
            page.draw_rect((x,100,x+80,160),color=(0,0,0))
            page.insert_text((x+10,132),label,fontsize=10)
        page.draw_line((180,130),(250,130),color=(0,0,0))
        page.draw_line((250,130),(242,125),color=(0,0,0))
        page.draw_line((250,130),(242,135),color=(0,0,0))
        page.insert_text((60,210),'Table 1: Operating limits',fontsize=12)
        for x in (60,240,450): page.draw_line((x,240),(x,330),color=(0,0,0))
        for y in (240,270,300,330): page.draw_line((60,y),(450,y),color=(0,0,0))
        for i,(left,right) in enumerate((('Mode','Limit'),('Mode A','3.0 V'),('Mode B','2.5 V'))):
            page.insert_text((70,260+30*i),left,fontsize=11)
            page.insert_text((250,260+30*i),right,fontsize=11)
        prose='Normal prose following both objects shall remain visible.'
        page.insert_text((60,380),prose,fontsize=11)
        path=root/'objects.pdf';document.save(path)
    cases.append(dict(id='objects',path=str(path),pages=[1],expect={'pages':[dict(page=1,contains=['3.0 V','2.5 V',prose],
        regions=[dict(labels=['table'],point=[250,280]),dict(labels=['picture','image','figure'],point=[130,130])])]}))
    (root/'manifest.json').write_text(json.dumps({'cases':cases},ensure_ascii=False,indent=2)+'\n')
    print(root/'manifest.json')


if __name__ == '__main__':
    main()
