# text_renderer/__init__.py
# 暴露 text_renderer 包对外的公共接口

from .fonts import (
    BitmapFont,
    MultiChunkFont,
    V4Font,
    MultiChunkV4Font,
    get_font,
    get_multi_font,
    clear_font_cache,
    list_available_sizes,
    _detect_version,
    _create_font_direct,
)

from .layout import (
    parse_color_spec,
    _parse_default_style,
    _parse_per_char,
)

from .render import (
    render_full,
    render_and_display,
    render_and_display_stream,
)

# 兼容旧代码的一些常量，如果需要的话可以保留
W = 800
H = 480
FRAME_BYTES = W * H // 2
SENTINEL = 0xFF

__all__ = [
    'BitmapFont',
    'MultiChunkFont',
    'V4Font',
    'MultiChunkV4Font',
    'get_font',
    'get_multi_font',
    'clear_font_cache',
    'list_available_sizes',
    '_detect_version',
    '_create_font_direct',
    'parse_color_spec',
    '_parse_default_style',
    '_parse_per_char',
    'render_full',
    'render_and_display',
    'render_and_display_stream',
    'W', 'H', 'FRAME_BYTES', 'SENTINEL'
]