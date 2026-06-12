from ctxlineage._stack import stack_summary


def _user_function():
    return stack_summary()


def test_includes_calling_user_function():
    frames = _user_function()
    assert any(":_user_function:" in f for f in frames)


def test_excludes_ctxlineage_frames():
    frames = _user_function()
    assert not any(f.startswith("_stack.py:") for f in frames)


def test_respects_limit():
    def depth3():
        return stack_summary(limit=2)

    def depth2():
        return depth3()

    def depth1():
        return depth2()

    assert len(depth1()) == 2


def test_innermost_user_frame_first():
    frames = _user_function()
    assert ":_user_function:" in frames[0]
