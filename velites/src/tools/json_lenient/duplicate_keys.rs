//! Lossless object-key preservation for the lenient JSON salvage parse.
//! Duplicate keys are declined before `Value` map semantics can discard an
//! earlier value; syntax errors remain non-candidates for salvage.

use std::collections::HashSet;
use std::fmt;

use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::Deserializer as _;
use serde_json::Value;

/// [`parse_detecting_duplicate_keys`] 的失败分类：重复键（与宽容解析的
/// 无损承诺冲突，放弃解析并注记原因）与语法错误（本就不构成候选，错误
/// 细节不消费——调用方只区分「整串不是合法 JSON」这一种结局）。
pub(super) enum Decline {
    /// 任一层对象出现重复键：`Value` 的 map 语义只保留最后一个，继续就
    /// 是静默改坏输入。
    DuplicateKey,
    /// 语法错误：非合法 JSON 文本（或值后有多余内容），不构成二次编码
    /// 候选。
    Syntax,
}

pub(super) fn parse_detecting_duplicate_keys(text: &str) -> Result<Value, Decline> {
    let mut deserializer = serde_json::Deserializer::from_str(text);
    let parsed = deserializer
        .deserialize_any(DuplicateKeyVisitor)
        .map_err(|err| match err.classify() {
            // 重复键识别钉住 serde_json 的 Display 契约：错误格式为
            // "{msg} at line {L} column {C}"，`de::Error::custom` 的自定义
            // 消息（classify 必为 Data，即 ErrorCode::Message）原样构成
            // 前缀，故前缀匹配按设计成立，不去解析行列号。serde_json 若改
            // Display 格式，兜底是测试变红而非静默误判——
            // velites/tests/json_lenient.rs 的
            // set_duplicate_object_keys_stay_literal 会直接失败。
            serde_json::error::Category::Data if err.to_string().starts_with(DUPLICATE_KEY_MSG) => {
                Decline::DuplicateKey
            }
            _ => Decline::Syntax,
        })?;
    // `serde_json::from_str` 的完整语义：值之后只许空白，`[1,2] extra`
    // 这类残缺文本必须整串拒绝（NotCandidate），不能只解析前缀。
    deserializer
        .end()
        .map_err(|_| Decline::Syntax)
        .map(|()| parsed)
}

/// 重复键的自定义错误文案（`de::Error::custom` 的消息）：classify 必为
/// Data（ErrorCode::Message），定位后缀按 Display 的实际输出比对。
const DUPLICATE_KEY_MSG: &str = "duplicate object key";

/// 递归构造 `Value` 的 Visitor（#747 重复键修复，codex review）：与
/// serde_json 的 `Value` 反序列化同一形状，唯一分岔在对象键——
/// `next_key` 回来先查本层 `HashSet` 再 `next_value`，在重复键被 map
/// 吞掉**之前**就报错（serde 默认路径只保留最后一个键值，宽容解析就
/// 会静默改坏此前按字面保存的输入）。键比较用解析后的字符串：
/// `"a"` 与 `"\u0061"` 是同一个键，serde_json 写回时也这么合并；字符串
/// 值经解析天然无损（`\"` / `\uXXXX` 等转义形式等价），无需同款校验。
/// 数字的降级拒绝不在 visitor 做——那是 `numbers_round_trip` 的职责，
/// 保持单一职责与既有行为不变。
struct DuplicateKeyVisitor;

/// Visitor 的递归种子（`DeserializeSeed`）：让容器内的每个 value 都回到
/// `deserialize_any(DuplicateKeyVisitor)`，而不是 serde_json 默认的 `Value`
/// 反序列化（那条路不查重复键）。
struct Seed;

impl<'de> de::DeserializeSeed<'de> for Seed {
    type Value = Value;

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: de::Deserializer<'de>,
    {
        deserializer.deserialize_any(DuplicateKeyVisitor)
    }
}

impl<'de> Visitor<'de> for DuplicateKeyVisitor {
    type Value = Value;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("any valid JSON value")
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::Null)
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::Bool(value))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::Number(value.into()))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::Number(value.into()))
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        // 防御位：serde_json 对溢出文本（如 1e999）在解析层就报
        // number out of range，非有限值到不了这里；真到了也不 panic。
        let number = serde_json::Number::from_f64(value)
            .ok_or_else(|| de::Error::custom("non-finite number"))?;
        Ok(Value::Number(number))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::String(value.to_owned()))
    }

    fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut values = Vec::new();
        // 元素/值一律 `deserialize_any(DuplicateKeyVisitor)` 递归——泛型
        // 参数必须显式钉住：写 `next_value()` 会被推断成 serde_json 自带
        // 的 `Value` 反序列化（用不到本 visitor），嵌套层的重复键就漏检。
        while let Some(value) = seq.next_element_seed(Seed)? {
            values.push(value);
        }
        Ok(Value::Array(values))
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut seen = HashSet::new();
        let mut entries = serde_json::Map::new();
        while let Some(key) = map.next_key::<String>()? {
            if !seen.insert(key.clone()) {
                return Err(de::Error::custom(DUPLICATE_KEY_MSG));
            }
            entries.insert(key, map.next_value_seed(Seed)?);
        }
        Ok(Value::Object(entries))
    }
}
