# web_server/handlers_text.py — 处理流式文字渲染接口
import gc
import json
import epd

from .utils import (
    _send_json,
    _read_body,
    EpdStreamAdapter,
)
from text_renderer import (
    render_and_display_stream,
    render_full,
    _create_font_direct,
)


def _norm_mode(raw):
    if raw in ('full', 'fast', 'partial'):
        return raw
    return 'full'


def handle_text_render(conn, headers, body_prefix):
    gc.collect()

    try:
        body = _read_body(conn, headers, body_prefix)
        _gc = gc
        _gc.collect()

        try:
            params = json.loads(body)
        except MemoryError:
            print("[TEXT] OOM in json.loads, body_size=%d" % len(body))
            raise

        del body
        _gc.collect()

        size = int(params.get("size", 32))
        font_path = params.get("font_path") or None
        font_paths = params.get("font_paths") or None
        per_char = params.get("per_char")
        mode = _norm_mode(params.get("mode", "full"))

        default_key = '|'.join(font_paths) if font_paths else ''
        extra_fonts = {}
        if per_char:
            fp_set = set()
            for v in per_char.values():
                if isinstance(v, dict) and v.get('font_paths'):
                    fp = v['font_paths']
                    if fp:
                        key = '|'.join(fp)
                        if key == default_key:
                            continue
                        fp_set.add(key)

            MAX_EXTRA = 2
            if len(fp_set) > MAX_EXTRA:
                fp_set = set(list(fp_set)[:MAX_EXTRA])

            for key in fp_set:
                fp = key.split('|')
                f = _create_font_direct(fp)
                if f:
                    extra_fonts[key] = f
                    _gc.collect()

        args = dict(
            size=size,
            direction=int(params.get("direction", 0)),
            per_char=per_char,
            default_style=params.get("default_style"),
            align=params.get("align", "left"),
            valign=params.get("valign", "top"),
            line_height=float(params.get("line_height", 1.5)),
            margin_top=int(params.get("margin_top", 20)),
            margin_bottom=int(params.get("margin_bottom", 20)),
            margin_left=int(params.get("margin_left", 20)),
            margin_right=int(params.get("margin_right", 20)),
            font_path=font_path,
            font_paths=font_paths,
            extra_fonts=extra_fonts,
            line_extras=params.get("line_extras") or None,
        )

        text = params.get("text", "")
        background = params.get("background") or None
        dry_run = bool(params.get("dry_run", False))

        try:
            conn.settimeout(300)
        except Exception:
            pass

        if dry_run:
            bin_data = render_full(text, background=background, **args)
            from .utils import _send
            _send(conn, b"200 OK", bin_data, content_type=b"application/octet-stream")
        else:
            band = int(params.get("band_rows", 8))

            _send_json(conn, {"ok": True, "mode": "stream",
                              "band_rows": band, "bg": background,
                              "extra_fonts": len(extra_fonts),
                              "refresh_mode": mode,
                              "started": True})

            adapter = EpdStreamAdapter(epd)
            try:
                render_and_display_stream(adapter, text, band_rows=band,
                                          background=background,
                                          mode=mode, **args)
            except Exception as render_err:
                print("[TEXT] deferred render fail:", render_err)
                try:
                    import sys as _sys
                    _sys.print_exception(render_err)
                except Exception:
                    pass

    except Exception as e:
        print("[TEXT] error:", e)
        try:
            _send_json(conn, {"ok": False, "error": str(e)}, b"500 Internal Server Error")
        except Exception:
            pass