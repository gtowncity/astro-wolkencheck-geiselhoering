from nowcast_service.satellite_image import _label_font, _label_text


def test_runtime_font_supports_satellite_unicode_labels() -> None:
    font, unicode_supported = _label_font(21)
    text = "Geiselhöring · Infrarot 10,5 µm"

    assert unicode_supported is True
    assert font.getbbox(text) is not None
    assert _label_text(text, unicode_supported=True) == text


def test_readable_ascii_fallback_never_uses_replacement_glyphs() -> None:
    assert _label_text(
        "Geiselhöring · Infrarot 10,5 µm",
        unicode_supported=False,
    ) == "Geiselhoering - Infrarot 10,5 um"
