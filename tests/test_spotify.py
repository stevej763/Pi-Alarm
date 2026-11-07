from app.spotify import _same_name


def test_device_names_match_ignoring_apostrophe_style_and_case():
    assert _same_name("Steve\u2019s MacBook Air", "steve's macbook air")
    assert _same_name("Radio Alarm", "radio  alarm")
    assert not _same_name("Radio Alarm", "Kitchen")
