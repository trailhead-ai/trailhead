from tally.cli import main


def test_main_prints_words(tmp_path, capsys):
    path = tmp_path / "a.txt"
    path.write_text("one two")
    main(["tally", str(path)])
    assert capsys.readouterr().out == "words: 2\n"
