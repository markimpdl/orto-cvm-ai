import numpy as np

from cvm.voice import qa
from cvm.voice.text import prepare, split_sentences


def test_numbers_pt():
    out = prepare("Em 2026, o preço subiu 25% para R$ 1.500,50 no 1º dia.", "pt-BR")
    assert "dois mil e vinte e seis" in out
    assert "vinte e cinco por cento" in out
    assert "mil e quinhentos" in out and "reais" in out
    assert "primeiro dia" in out
    assert not any(c.isdigit() for c in out)


def test_numbers_en_and_ar():
    assert "twenty twenty-six" in prepare("Back in 2026 it cost $3.5", "en-US")
    assert "dollars" in prepare("It cost $3.5", "en-US")
    ar = prepare("في عام ٢٠٢٦ ارتفع ٢٥٪", "ar-AE")
    assert not any(c.isdigit() for c in ar)


def test_split_sentences_keeps_abbreviations_and_merges_fragments():
    s = split_sentences("O Dr. Silva chegou cedo. Sim. Depois tudo mudou para sempre!", "pt-BR")
    assert s == ["O Dr. Silva chegou cedo. Sim.", "Depois tudo mudou para sempre!"]


def test_split_long_sentence_at_commas():
    long = ", ".join(["uma frase bem comprida com muitas palavras"] * 10) + "."
    parts = split_sentences(long, "pt-BR")
    assert len(parts) > 1 and all(len(p) <= 230 for p in parts)


def test_arabic_question_mark_splits():
    assert len(split_sentences("ماذا حدث هنا بالضبط؟ لا أحد يعرف الحقيقة كاملة.", "ar-AE")) == 2


def test_wer_and_loops():
    assert qa.word_error_rate("o gato subiu no telhado", "O gato subiu no telhado!", "pt-BR") == 0
    assert qa.word_error_rate("o gato subiu no telhado", "o gato no", "pt-BR") == 0.4
    assert qa.word_error_rate("مَرْحَبًا بِكُمْ", "مرحبا بكم", "ar-AE") == 0
    assert qa.loop_artifact("e então e então e então e então acabou", "pt-BR")
    assert not qa.loop_artifact("uma frase normal sem repetição nenhuma", "pt-BR")


def test_trim_edges():
    sr = 24000
    tone = 0.3 * np.sin(np.linspace(0, 400 * np.pi, sr)).astype("float32")
    audio = np.concatenate([np.zeros(sr), tone, np.zeros(sr)]).astype("float32")
    out = qa.trim_edges(audio, sr)
    assert abs(len(out) / sr - 1.08) < 0.03


def test_abbreviations_read_in_full():
    assert prepare("O Dr. Silva e a Sra. Lima, etc.", "pt-BR") == "O Doutor Silva e a Senhora Lima, etcétera"
    assert prepare("Mr. Smith vs. Dr. Who", "en-US") == "Mister Smith versus Doctor Who"


def test_pronunciation_lexicon_applies_whole_words_first():
    lex = {"GTA": "Gê Tê A", "Take-Two": "Teik Tu"}
    assert prepare("A Take-Two lançou GTA 6, não GTAX.", "pt-BR", lex) == "A Teik Tu lançou Gê Tê A seis, não gê tê á xis."


def test_arabic_numbers_spoken_gulf_style_and_decimals():
    assert prepare("7.6 إنش", "ar-AE") == "سبعة فاصلة ستة إنش"
    assert prepare("في 2027 بسعر 8,499 درهم", "ar-AE") == "في ألفين وسبعة وعشرين بسعر ثمانية آلاف وأربعمية وتسعة وتسعين درهم"
    assert prepare("0.05 و 200", "ar-AE") == "صفر فاصلة صفر خمسة و ميتين"


def test_acronyms_spelled_in_the_narration_language():
    assert prepare("A IA nos EUA, Carlos III e a NASA.", "pt-BR") == "A i á nos é u á, Carlos III e a NASA."
    assert prepare("The AI from the UAE.", "en-US") == "The ay eye from the you ay ee."
    assert prepare("NASA و GPU", "ar-AE") == "ناسا و جي بي يو"


def test_pt_dates():
    assert prepare("No dia 12/11/2026", "pt-BR") == "No dia doze de novembro de dois mil e vinte e seis"
    assert prepare("em 2/12/1971", "pt-BR") == "em dois de dezembro de mil novecentos e setenta e um"
    assert prepare("em 12/11", "pt-BR") == "em doze de novembro"
    assert prepare("em 01/05/2026", "pt-BR") == "em primeiro de maio de dois mil e vinte e seis"
    assert prepare("o 1º de maio", "pt-BR") == "o primeiro de maio"
    assert prepare("placar 45/13", "pt-BR") != "placar quarenta e cinco de treze"  # not a valid day/month


def test_pt_times():
    assert prepare("às 18:30", "pt-BR") == "às dezoito e trinta"
    assert prepare("às 18:00", "pt-BR") == "às dezoito horas"
    assert prepare("às 18h30", "pt-BR") == "às dezoito e trinta"
    assert prepare("às 18h", "pt-BR") == "às dezoito horas"
    assert prepare("às 9:05", "pt-BR") == "às nove e cinco"
    assert prepare("à 1:00 e às 22h", "pt-BR") == "à uma hora e às vinte e duas horas"


def test_pt_versions_thousands_and_decimals():
    assert prepare("iOS 18.2", "pt-BR") == "iOS dezoito ponto dois"
    assert prepare("versão 2.1.3", "pt-BR") == "versão dois ponto um ponto três"
    assert prepare("1.250 comentários", "pt-BR") == "mil duzentos e cinquenta comentários"
    assert prepare("4,5 milhões", "pt-BR") == "quatro vírgula cinco milhões"
    assert prepare("4,5%", "pt-BR") == "quatro vírgula cinco por cento"
    assert prepare("35%", "pt-BR") == "trinta e cinco por cento"


def test_pt_big_numbers_have_no_commas():
    assert prepare("Em 1971", "pt-BR") == "Em mil novecentos e setenta e um"
    assert prepare("Em 2026", "pt-BR") == "Em dois mil e vinte e seis"
    assert prepare("1.200.000 pessoas", "pt-BR") == "um milhão e duzentos mil pessoas"


def test_pt_money():
    assert prepare("R$ 349,90", "pt-BR") == "trezentos e quarenta e nove reais e noventa centavos"
    assert prepare("R$ 1,00", "pt-BR") == "um real"
    assert prepare("R$ 2 mil", "pt-BR") == "dois mil reais"
    assert prepare("R$ 0,50", "pt-BR") == "cinquenta centavos"
    assert prepare("US$ 10 ou $10", "pt-BR") == "dez dólares ou dez dólares"
    assert prepare("AED 50 ou 50 dirhams", "pt-BR") == "cinquenta dirhams ou cinquenta dirhams"


def test_pt_tech_units_and_products():
    assert prepare("32 GB, 2 TB, 16 MB, 1 GB", "pt-BR") == "trinta e dois gigas, dois teras, dezesseis megas, um giga"
    assert prepare("4K e 8K", "pt-BR") == "quatro K e oito K"
    assert prepare("120 fps a 120Hz ou 120 Hz", "pt-BR") == "cento e vinte éfe pê ésse a cento e vinte hertz ou cento e vinte hertz"
    assert prepare("PS5 e PS4", "pt-BR") == "PlayStation cinco e PlayStation quatro"
    assert prepare("RTX 5090", "pt-BR") == "érre tê xis cinquenta noventa"
    assert prepare("RTX 4070", "pt-BR") == "érre tê xis quarenta setenta"
    assert prepare("RX 7900", "pt-BR") == "érre xis setenta e nove cem"
    assert prepare("GTX 1080", "pt-BR") == "gê tê xis dez oitenta"
    assert prepare("iPhone 16", "pt-BR") == "iPhone dezesseis"
    assert prepare("O GTA 6", "pt-BR") == "O gê tê á seis"
    assert prepare("O Ep. 3", "pt-BR") == "O episódio três"


def test_pt_english_words_kept():
    s = "deploy, update, framework, feedback e ray tracing"
    assert prepare(s, "pt-BR") == s


def test_en_money_dates_times():
    assert prepare("$449.99", "en-US") == "four hundred forty-nine dollars and ninety-nine cents"
    assert prepare("$1", "en-US") == "one dollar"
    assert prepare("On 11/12/2026", "en-US") == "On November twelfth, twenty twenty-six"
    assert prepare("March 3rd, 2025", "en-US") == "March third, twenty twenty-five"
    assert prepare("at 9:30 am", "en-US") == "at nine thirty a m"
    assert prepare("at 9:00", "en-US") == "at nine o'clock"
    assert prepare("at 18:30", "en-US") == "at eighteen thirty"
    assert prepare("at 9:05", "en-US") == "at nine oh five"
    assert prepare("1,250 fans", "en-US") == "one thousand two hundred fifty fans"


def test_en_tech_and_products():
    assert prepare("32 GB at 4K and 8K", "en-US") == "thirty-two gigabytes at four K and eight K"
    assert prepare("120 fps", "en-US") == "one hundred twenty eff pee ess"
    assert prepare("PS5 Pro", "en-US") == "PlayStation five Pro"
    assert prepare("RTX 5090, RTX 4070, RX 7900, GTX 1080", "en-US") == (
        "ar tee ex fifty ninety, ar tee ex forty seventy, ar ex seventy-nine hundred, jee tee ex ten eighty")
    assert prepare("Ep. 3", "en-US") == "episode three"


def test_sample_sentences():
    assert prepare("O GTA 6 sai em 26 de maio de 2026, às 18:30, por R$ 349,90.", "pt-BR") == (
        "O gê tê á seis sai em vinte e seis de maio de dois mil e vinte e seis, às dezoito e trinta, "
        "por trezentos e quarenta e nove reais e noventa centavos.")
    assert prepare("Released March 3rd, 2025 at 9:30 am.", "en-US") == (
        "Released March third, twenty twenty-five at nine thirty a m.")
