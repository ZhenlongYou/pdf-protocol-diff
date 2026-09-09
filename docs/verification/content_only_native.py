"""Run the actual native JavaScript bridge and inspect its generated report."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from content_only_probe import make_pdf
from content_only_oracle import assert_report
from protocol_pdf_diff.webview_gui import create_webview_window, select_webview_backend
import webview


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    case = json.loads((ROOT/'docs/verification/fixtures/content-only/realistic.json').read_text())['cases'][0]
    old, new = output/'old.pdf', output/'new.pdf'
    make_pdf(old, case['old_pages']); make_pdf(new, case['new_pages'])
    window, api = create_webview_window()
    errors = []
    def verify():
        try:
            assert window.events.loaded.wait(15)
            config = dict(old_pdf=str(old), new_pdf=str(new), output_dir=str(output/'reports'), min_similarity='0.72', max_snippets='20', auto_open=False)
            window.evaluate_js('window.protocolDiff.receive({type: "running"}); window.pywebview.api.run_comparison('+json.dumps(config)+')')
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                state = api.get_run_state()
                if state['type'] in ('success','error','cancelled'):
                    break
                time.sleep(.2)
            assert state['type'] == 'success', state
            report = Path(state['html_path'])
            assert_report(case, (report.parent/'protocol_diff_report.md').read_text(), (report.parent/'changes.csv').read_text()+(report.parent/'table_changes.csv').read_text())
            time.sleep(.5)  # Allow the native renderer's normal state poll.
            state['visible_status'] = window.evaluate_js('document.getElementById("status").innerText')
            assert state['visible_status'], state
            (output/'native-result.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
            print('CONTENT_NATIVE_OK', report)
        except Exception as error:
            errors.append(error)
        finally:
            window.destroy()
    webview.start(verify, gui=select_webview_backend(), private_mode=True)
    if errors:
        raise errors[0]

if __name__=='__main__':
    main()
