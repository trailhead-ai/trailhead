from tally.counter import count_words


def test_count_words(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("one two three")
    assert count_words(str(path)) == 3
