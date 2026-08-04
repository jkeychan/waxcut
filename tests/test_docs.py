import inspect

import waxcut

MIN_DOCSTRING_LENGTH = 40  # long enough to rule out placeholder one-liners


def test_every_public_name_has_a_real_docstring():
    undocumented = []
    for name in waxcut.__all__:
        obj = getattr(waxcut, name)
        doc = inspect.getdoc(obj)
        if not doc or len(doc) < MIN_DOCSTRING_LENGTH:
            undocumented.append(name)
    assert not undocumented, f"Missing/thin docstrings: {undocumented}"
