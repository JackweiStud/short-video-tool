from generate_full_video import build_parser


def test_generate_full_video_defaults_to_auto_subtitle_detection():
    args = build_parser().parse_args([])
    assert args.subtitle_status == "auto"
