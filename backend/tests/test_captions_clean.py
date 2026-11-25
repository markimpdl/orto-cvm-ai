from cvm.render.captions import clean_text, has_arabic


def test_emoji_removed_and_arabic_detected():
    assert clean_text("❌ Face ID  ❌ Telephoto ✅ 🔥") == "Face ID Telephoto"
    assert clean_text("بصمةُ الوجهِ ٢٠٢٦؟") == "بصمةُ الوجهِ ٢٠٢٦؟"
    assert has_arabic("ليوا 2027") and not has_arabic("Liwa 2027")
