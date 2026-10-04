"""REX plugin for the private Policenauts Disc 1 text-only translation JSON.

Select policenauts_texts_tr_edit_audited.json in REX. Its sibling
policenauts_texts_master_audited.json supplies the original English text.
The output remains a plain JSON array, in the same order as the input.
"""

import json
from pathlib import Path


GAME_CONTEXT_PROMPT = (
    "Policenauts, Jonathan Ingram'ın Beyond Coast uzay kolonisinde yürüttüğü "
    "soruşturmayı anlatan bilimkurgu ve polisiye bir maceradır. Bu veri setinde "
    "karakter diyalogları, sesli sahne altyazıları, film altyazıları, menü "
    "etiketleri ve sistem mesajları bulunur. Kısa kayıtlar bazen ardışık "
    "cümle parçalarıdır."
)

TRANSLATION_PROMPT = (
    "Bu veri setindeki | işareti oyun metninin alt satıra geçişidir; gerçek "
    "fiziksel satır sonu değildir. Türkçe söz dizimine göre yeri değişebilir "
    "ve gerekmiyorsa kullanılmayabilir. %d oyun tarafından doldurulan sayıdır; "
    "bir metinde birden fazla geçerse her biri ayrı yer tutucudur. Görünen "
    "metindeki bunun dışındaki noktalama işaretleri normal metindir."
)

TERM_PROMPT = ""

MASTER_FILENAME = "policenauts_texts_master_audited.json"
VALID_SOURCES = {"game_sz", "jxs_voice", "movie_ascii", "bin_ascii"}


def get_file_encoding(filepath):
    return "utf-8"


def _load_edit(content):
    data = json.loads(content)
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError("Policenauts metin dosyası yalnızca string içeren JSON dizisi olmalı")
    return data


def _load_master(filepath, count):
    path = Path(filepath).with_name(MASTER_FILENAME)
    if not path.is_file():
        raise ValueError(f"Aynı klasörde {MASTER_FILENAME} bulunamadı")
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or len(entries) != count:
        raise ValueError("Master JSON ile düzenlenebilir JSON metin sayısı eşleşmiyor")
    for index, entry in enumerate(entries):
        if (not isinstance(entry, dict)
                or entry.get("index") != index
                or entry.get("source") not in VALID_SOURCES
                or not isinstance(entry.get("text_with_breaks"), str)):
            raise ValueError(f"Geçersiz master metin kaydı: {index}")
    return entries


def _key(index):
    return f"p{index:05d}"


def _show(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "|")


def extract_translatable_texts(content, filepath, extraction_context=None):
    data = _load_edit(content)
    entries = _load_master(filepath, len(data))
    result = {}
    for index, entry in enumerate(entries):
        source = entry["text_with_breaks"]
        if source.strip():
            result[_key(index)] = (_show(source), {"source": entry["source"]})
    return result


def _restore(answer):
    value = answer.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    return value.replace("|", "\n")


def rebuild_content_with_translations(original_content, original_texts, translated_texts):
    data = _load_edit(original_content)
    for index in range(len(data)):
        key = _key(index)
        answer = translated_texts.get(key)
        if not isinstance(answer, str) or not answer.strip():
            continue
        # Unchanged model output should not replace an existing Turkish edit
        # with the English source. Optional breaks/placeholders are not required.
        if answer == original_texts.get(key):
            continue
        data[index] = _restore(answer)
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
