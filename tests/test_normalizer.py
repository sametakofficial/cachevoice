from cachevoice.cache.normalizer import normalize, turkish_lower
from cachevoice.config import NormalizeConfig


def test_turkish_i_lower():
    assert turkish_lower("I") == "ı"
    assert turkish_lower("İ") == "i"
    assert turkish_lower("IŞIK") == "ışık"
    assert turkish_lower("İSTANBUL") == "istanbul"


def test_diacritic_folding():
    assert normalize("çok güzel") == normalize("cok guzel")
    assert normalize("IĞDIR") == normalize("igdir")
    assert normalize("şehir") == normalize("sehir")
    assert normalize("görmüş") == normalize("gormus")


def test_number_replacement():
    assert normalize("3 kaynak buldum") == normalize("5 kaynak buldum")
    assert normalize("10 sonuç var") == normalize("2 sonuç var")


def test_whitespace_punctuation():
    assert normalize("Araştırıyorum!") == normalize("araştırıyorum")
    assert normalize("  çok   güzel  ") == normalize("cok guzel")


def test_cache_hit_scenarios():
    assert normalize("Hemen bakıyorum") == normalize("hemen bakıyorum")
    assert normalize("3 kaynak buldum, analiz ediyorum") == normalize("5 kaynak buldum analiz ediyorum")
    assert normalize("Araştırıyorum...") == normalize("Araştırıyorum")


def test_edge_cases():
    assert normalize("") == ""
    assert normalize("   ") == ""
    assert normalize("123") == "#"
    assert normalize("İİİ") == normalize("iii")
    assert normalize("IIı") == normalize("ııı")


def test_minimax_pause_markers_stripped():
    assert normalize("Merhaba<#2.4#> nasılsın") == normalize("Merhaba nasılsın")
    assert normalize("<#0.5#>Selam<#1.0#>") == normalize("Selam")
    assert normalize("bir<#3.14#> iki <#0.1#>üç") == normalize("bir iki üç")


def test_minimax_interjection_tags_stripped():
    assert normalize("(gasps) ne oldu") == normalize("ne oldu")
    assert normalize("tamam (laughs) anladım") == normalize("tamam anladım")
    assert normalize("(sighs)(coughs) evet") == normalize("evet")


def test_minimax_all_interjections():
    interjections = [
        "gasps", "laughs", "sighs", "coughs", "clears_throat",
        "chuckles", "sniffs", "yawns", "groans", "hums",
        "surprised", "relieved", "disgusted", "scared", "nervous",
        "curious", "confused", "excited", "sad",
    ]
    for tag in interjections:
        assert normalize(f"({tag}) test") == normalize("test")


def test_minimax_combined_with_other_transforms():
    result = normalize("(laughs) Merhaba<#2.0#> 3 kişi geldi!")
    assert result == normalize("merhaba 5 kisi geldi")


def test_minimax_disabled():
    cfg = NormalizeConfig(strip_minimax=False)
    result = normalize("hello<#2.4#>world", cfg)
    assert "#" in result


def test_config_lowercase_disabled():
    cfg = NormalizeConfig(lowercase=False)
    result = normalize("Hello World", cfg)
    assert "Hello" in result


def test_config_strip_punctuation_disabled():
    cfg = NormalizeConfig(strip_punctuation=False)
    result = normalize("hello, world!")
    assert "hello world" == normalize("hello, world!")
    result_no_strip = normalize("hello, world!", cfg)
    assert "," in result_no_strip


def test_config_collapse_whitespace_disabled():
    cfg = NormalizeConfig(collapse_whitespace=False)
    result = normalize("hello   world", cfg)
    assert "   " in result


def test_config_replace_numbers_disabled():
    cfg = NormalizeConfig(replace_numbers=False)
    result = normalize("3 kaynak buldum", cfg)
    assert "3" in result
    assert "#" not in result


def test_config_all_disabled():
    cfg = NormalizeConfig(
        lowercase=False,
        strip_punctuation=False,
        collapse_whitespace=False,
        replace_numbers=False,
        strip_minimax=False,
    )
    result = normalize("  Hello, World!  <#2.0#> (laughs) 42  ", cfg)
    assert "Hello" in result
    assert "," in result
    assert "42" in result
    assert "<#2.0#>" in result
    assert "(laughs)" in result


def test_default_config_backward_compatible():
    cfg = NormalizeConfig()
    assert normalize("Merhaba Dünya!", cfg) == normalize("Merhaba Dünya!")
