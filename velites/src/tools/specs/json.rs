use serde_json::Value;

pub(super) fn spec() -> (&'static str, Value) {
    (
        "Read or modify one field of a JSON file via a JSON path — the \
         read-modify-write primitive for patching large JSON artifacts \
         you produced. NEVER rewrite a whole JSON file (write tool) to \
         change one field, and NEVER shell out to python for this. `get` \
         returns the value at the path (null when absent). `set` writes \
         any JSON value at the path and saves the file (compactly, \
         atomically; the value is capped at the 4MB whole-file limit). \
         `delete` removes the key/array element at the path. Paths: \
         dotted keys and [index] segments, e.g. `steps[2].content` or \
         `[\"a key.with.dots\"].sub`; missing intermediate keys are an \
         error for set/delete (no auto-create), and get reports null \
         instead.",
        serde_json::json!({
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["get", "set", "delete"], "description": "Operation to perform."},
                "path": {"type": "string", "description": "JSON file path, relative to the working directory."},
                "query": {"type": "string", "description": "JSON path to the field, e.g. `steps[2].content` or `[\"a key\"].sub` (max 512 chars)."},
                // #747：value 可以是任意 JSON 标量，JSON Schema 无简洁的
                // 「any」写法——保持无 type（宽松类型，两 provider 均原样
                // 透传 schema），description 用正反例钉住「容器直接以
                // JSON 值传入」，并声明运行时的宽容解析行为（含无损与
                // 闸内两个前提，见 json_lenient.rs 的
                // parse_double_encoded_container）。
                "value": {"description": "set: any JSON value to write at the path \
            (objects/arrays/strings/numbers/booleans/null). Pass containers directly as \
            JSON — value: [\"1.5\", \"2.5\"] or {\"k\": 1} — never as a string holding \
            JSON text like \"[\\\"1.5\\\", \\\"2.5\\\"]\". A value string that parses \
            losslessly as a JSON array/object (within a size gate) is parsed as that \
            container before writing; container text with lossy numbers, duplicate \
            object keys, or over the gate stays literal, with a note saying so. To \
            store container-shaped text literally, wrap it in an object (e.g. \
            {\"text\": \"...\"}); rewriting the whole file with the `write` tool is \
            only a last resort."}
            },
            "required": ["op", "path", "query"]
        }),
    )
}
