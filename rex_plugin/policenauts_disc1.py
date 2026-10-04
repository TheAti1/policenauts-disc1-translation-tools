"""REX plugin for ordered Policenauts English-source translation JSON.

The preferred input is policenauts_rex_english.json, generated from the private
master catalog. The legacy plain-array edit JSON remains supported.
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
FORMAT = "policenauts-rex-v1"
KIND_BY_SOURCE = {
    "bin_ascii": "menu",
    "game_sz": "dialogue",
    "jxs_voice": "voiced_dialogue",
    "movie_ascii": "movie_subtitle",
}


def get_file_encoding(filepath):
    return "utf-8"


def _load_edit(content):
    data = json.loads(content)
    if isinstance(data, list):
        if not all(isinstance(item, str) for item in data):
            raise ValueError("Eski Policenauts JSON dizisi yalnızca string içermeli")
        return data, "legacy"
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"{FORMAT} biçimi veya eski string dizisi bekleniyor")
    if set(data) != {"format", "count", "source_language", "target_language", "entries"}:
        raise ValueError("REX JSON üst alanları geçersiz")
    entries = data.get("entries")
    if (not isinstance(entries, list)
            or type(data.get("count")) is not int
            or data["count"] != len(entries)
            or data.get("source_language") != "en"
            or data.get("target_language") != "tr"):
        raise ValueError("REX JSON başlığı veya toplam metin sayısı geçersiz")
    for index, row in enumerate(entries):
        if (not isinstance(row, dict)
                or set(row) != {"id", "index", "kind", "source", "translation"}
                or row.get("id") != _key(index)
                or type(row.get("index")) is not int
                or row["index"] != index
                or row.get("kind") not in KIND_BY_SOURCE.values()
                or not isinstance(row.get("source"), str)
                or (row["translation"] is not None
                    and not isinstance(row["translation"], str))):
            raise ValueError(f"REX metin kaydı veya alanları geçersiz: {index}")
    return data, "structured"


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
                or entry.get("source") not in KIND_BY_SOURCE
                or not isinstance(entry.get("text_with_breaks"), str)):
            raise ValueError(f"Geçersiz master metin kaydı: {index}")
    return entries


def _key(index):
    return f"p{index:05d}"


def _show(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "|")


def extract_translatable_texts(content, filepath, extraction_context=None):
    data, mode = _load_edit(content)
    rows = data if mode == "legacy" else data["entries"]
    entries = _load_master(filepath, len(rows))
    result = {}
    for index, entry in enumerate(entries):
        if mode == "structured":
            row = rows[index]
            if (row["source"] != entry["text_with_breaks"]
                    or row["kind"] != KIND_BY_SOURCE[entry["source"]]):
                raise ValueError(f"REX kaynağı master katalogla eşleşmiyor: {index}")
            source = row["source"]
        else:
            source = entry["text_with_breaks"]
        if source.strip():
            result[_key(index)] = (_show(source), {"kind": KIND_BY_SOURCE[entry["source"]]})
    return result


def _restore(answer):
    value = answer.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    return value.replace("|", "\n")


def rebuild_content_with_translations(original_content, original_texts, translated_texts):
    data, mode = _load_edit(original_content)
    rows = data if mode == "legacy" else data["entries"]
    for index in range(len(rows)):
        key = _key(index)
        answer = translated_texts.get(key)
        if not isinstance(answer, str) or not answer.strip():
            continue
        if mode == "structured":
            if answer == original_texts.get(key) and rows[index]["translation"] is not None:
                continue
            rows[index]["translation"] = _restore(answer)
        else:
            # Preserve existing Turkish edits when the model merely echoes English.
            if answer == original_texts.get(key):
                continue
            rows[index] = _restore(answer)
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
