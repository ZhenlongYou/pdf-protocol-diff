"""Exercise the actual desktop bridge on the two user-supplied complete PDFs."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from protocol_pdf_diff.webview_gui import create_webview_window, select_webview_backend
import webview


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    old, new, output = map(lambda p: Path(p).resolve(), sys.argv[1:4])
    output.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / 'main.py', *sorted((ROOT / 'src/protocol_pdf_diff').glob('*.py'))]
    before = {str(p): digest(p) for p in sources}
    inputs = {str(p): digest(p) for p in (old, new)}
    (output / 'start-identity.json').write_text(json.dumps(dict(sources=before, inputs=inputs), indent=2))
    window, api = create_webview_window()
    errors = []
    started = time.monotonic()

    def verify():
        try:
            assert window.events.loaded.wait(30)
            config = dict(old_pdf=str(old), new_pdf=str(new), output_dir=str(output / 'reports'),
                          min_similarity='0.72', max_snippets='20', auto_open=False)
            window.evaluate_js('window.protocolDiff.receive({type: "running"}); window.pywebview.api.run_comparison(' + json.dumps(config) + ')')
            deadline, next_log = time.monotonic() + 3600, time.monotonic()
            while time.monotonic() < deadline:
                state = api.get_run_state()
                if state['type'] in ('success', 'error', 'cancelled'):
                    break
                if time.monotonic() >= next_log:
                    print('RUNNING', round(time.monotonic() - started), flush=True)
                    next_log = time.monotonic() + 60
                time.sleep(1)
            assert state['type'] == 'success', state
            report = Path(state['html_path'])
            after = {str(p): digest(p) for p in sources}
            assert before == after, 'source changed during full comparison'
            assert inputs == {str(p): digest(p) for p in (old, new)}, 'input changed'
            receipt = dict(state=state, elapsed_seconds=time.monotonic() - started,
                           sources=after, inputs=inputs,
                           artifacts={str(p): digest(p) for p in report.parent.iterdir() if p.is_file()})
            (output / 'native-result.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print('CONTENT_CORRESPONDENCE_NATIVE_OK', report, flush=True)
        except Exception as error:
            errors.append(error)
        finally:
            window.destroy()
    webview.start(verify, gui=select_webview_backend(), private_mode=True)
    if errors:
        raise errors[0]


if __name__ == '__main__':
    main()
