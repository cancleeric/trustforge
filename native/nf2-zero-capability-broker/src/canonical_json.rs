use std::collections::BTreeMap;

#[derive(Debug, Clone, Eq, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Number(String),
    String(String),
    Array(Vec<Value>),
    Object(BTreeMap<String, Value>),
}

pub fn parse(bytes: &[u8]) -> Result<Value, &'static str> {
    let payload = bytes
        .strip_suffix(b"\n")
        .ok_or("canonical manifest requires one terminal LF")?;
    if payload.ends_with(b"\n") {
        return Err("canonical manifest has multiple terminal LFs");
    }
    let mut parser = Parser {
        bytes: payload,
        offset: 0,
    };
    let value = parser.value()?;
    if parser.offset != payload.len() || encode(&value).as_bytes() != payload {
        return Err("manifest is not canonical JSON");
    }
    Ok(value)
}

pub fn encode(value: &Value) -> String {
    match value {
        Value::Null => "null".into(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.clone(),
        Value::String(value) => format!("\"{}\"", escape(value)),
        Value::Array(values) => {
            let body = values.iter().map(encode).collect::<Vec<_>>().join(",");
            format!("[{body}]")
        }
        Value::Object(values) => {
            let body = values
                .iter()
                .map(|(key, value)| format!("\"{}\":{}", escape(key), encode(value)))
                .collect::<Vec<_>>()
                .join(",");
            format!("{{{body}}}")
        }
    }
}

fn escape(value: &str) -> String {
    let mut output = String::new();
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{08}' => output.push_str("\\b"),
            '\u{0c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value < '\u{20}' => {
                use std::fmt::Write;
                write!(output, "\\u{:04x}", value as u32).expect("write to String");
            }
            value if value.is_ascii() => output.push(value),
            value => {
                use std::fmt::Write;
                let scalar = value as u32;
                if scalar <= 0xffff {
                    write!(output, "\\u{scalar:04x}").expect("write to String");
                } else {
                    let adjusted = scalar - 0x10000;
                    let high = 0xd800 + (adjusted >> 10);
                    let low = 0xdc00 + (adjusted & 0x3ff);
                    write!(output, "\\u{high:04x}\\u{low:04x}").expect("write to String");
                }
            }
        }
    }
    output
}

struct Parser<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl Parser<'_> {
    fn value(&mut self) -> Result<Value, &'static str> {
        match self.peek()? {
            b'n' => {
                self.literal(b"null")?;
                Ok(Value::Null)
            }
            b't' => {
                self.literal(b"true")?;
                Ok(Value::Bool(true))
            }
            b'f' => {
                self.literal(b"false")?;
                Ok(Value::Bool(false))
            }
            b'"' => self.string().map(Value::String),
            b'[' => self.array(),
            b'{' => self.object(),
            b'-' | b'0'..=b'9' => self.number().map(Value::Number),
            _ => Err("invalid JSON value"),
        }
    }

    fn peek(&self) -> Result<u8, &'static str> {
        self.bytes.get(self.offset).copied().ok_or("truncated JSON")
    }

    fn literal(&mut self, expected: &[u8]) -> Result<(), &'static str> {
        if self.bytes.get(self.offset..self.offset + expected.len()) != Some(expected) {
            return Err("invalid JSON literal");
        }
        self.offset += expected.len();
        Ok(())
    }

    fn string(&mut self) -> Result<String, &'static str> {
        self.offset += 1;
        let mut output = String::new();
        loop {
            let byte = self.peek()?;
            match byte {
                b'"' => {
                    self.offset += 1;
                    return Ok(output);
                }
                b'\\' => {
                    self.offset += 1;
                    let escaped = self.peek()?;
                    self.offset += 1;
                    match escaped {
                        b'"' => output.push('"'),
                        b'\\' => output.push('\\'),
                        b'/' => output.push('/'),
                        b'b' => output.push('\u{08}'),
                        b'f' => output.push('\u{0c}'),
                        b'n' => output.push('\n'),
                        b'r' => output.push('\r'),
                        b't' => output.push('\t'),
                        b'u' => output.push(self.unicode_escape()?),
                        _ => return Err("invalid JSON escape"),
                    }
                }
                0..=0x1f => return Err("control character in JSON string"),
                _ => {
                    let remaining = std::str::from_utf8(&self.bytes[self.offset..])
                        .map_err(|_| "invalid UTF-8")?;
                    let character = remaining.chars().next().ok_or("truncated UTF-8")?;
                    output.push(character);
                    self.offset += character.len_utf8();
                }
            }
        }
    }

    fn unicode_escape(&mut self) -> Result<char, &'static str> {
        let first = self.hex4()?;
        if (0xd800..=0xdbff).contains(&first) {
            self.literal(b"\\u")?;
            let second = self.hex4()?;
            if !(0xdc00..=0xdfff).contains(&second) {
                return Err("invalid low surrogate");
            }
            char::from_u32(0x10000 + ((first - 0xd800) << 10) + second - 0xdc00)
                .ok_or("invalid Unicode scalar")
        } else if (0xdc00..=0xdfff).contains(&first) {
            Err("unpaired low surrogate")
        } else {
            char::from_u32(first).ok_or("invalid Unicode scalar")
        }
    }

    fn hex4(&mut self) -> Result<u32, &'static str> {
        let bytes = self
            .bytes
            .get(self.offset..self.offset + 4)
            .ok_or("truncated Unicode escape")?;
        self.offset += 4;
        bytes.iter().try_fold(0_u32, |value, byte| {
            byte.to_ascii_lowercase()
                .checked_sub(b'0')
                .filter(|digit| *digit < 10)
                .or_else(|| {
                    byte.to_ascii_lowercase()
                        .checked_sub(b'a')
                        .filter(|digit| *digit < 6)
                        .map(|digit| digit + 10)
                })
                .map(|digit| value * 16 + u32::from(digit))
                .ok_or("invalid Unicode escape")
        })
    }

    fn array(&mut self) -> Result<Value, &'static str> {
        self.offset += 1;
        let mut values = Vec::new();
        if self.peek()? == b']' {
            self.offset += 1;
            return Ok(Value::Array(values));
        }
        loop {
            values.push(self.value()?);
            match self.peek()? {
                b',' => self.offset += 1,
                b']' => {
                    self.offset += 1;
                    return Ok(Value::Array(values));
                }
                _ => return Err("invalid JSON array"),
            }
        }
    }

    fn object(&mut self) -> Result<Value, &'static str> {
        self.offset += 1;
        let mut values = BTreeMap::new();
        if self.peek()? == b'}' {
            self.offset += 1;
            return Ok(Value::Object(values));
        }
        loop {
            if self.peek()? != b'"' {
                return Err("object key is not a string");
            }
            let key = self.string()?;
            if self.peek()? != b':' {
                return Err("object colon absent");
            }
            self.offset += 1;
            let value = self.value()?;
            if values.insert(key, value).is_some() {
                return Err("duplicate object key");
            }
            match self.peek()? {
                b',' => self.offset += 1,
                b'}' => {
                    self.offset += 1;
                    return Ok(Value::Object(values));
                }
                _ => return Err("invalid JSON object"),
            }
        }
    }

    fn number(&mut self) -> Result<String, &'static str> {
        let start = self.offset;
        if self.peek()? == b'-' {
            self.offset += 1;
        }
        match self.peek()? {
            b'0' => self.offset += 1,
            b'1'..=b'9' => {
                self.offset += 1;
                while matches!(self.bytes.get(self.offset), Some(b'0'..=b'9')) {
                    self.offset += 1;
                }
            }
            _ => return Err("invalid JSON number"),
        }
        if matches!(self.bytes.get(self.offset), Some(b'.' | b'e' | b'E')) {
            return Err("non-integer manifest number");
        }
        let value = std::str::from_utf8(&self.bytes[start..self.offset])
            .map_err(|_| "invalid number")?
            .to_owned();
        if value == "-0" {
            return Err("noncanonical negative zero");
        }
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_round_trip() {
        let input = b"{\"a\":[true,null,1],\"b\":\"\\ud83d\\ude80\"}\n";
        assert_eq!(
            format!("{}\n", encode(&parse(input).unwrap())).as_bytes(),
            input
        );
    }

    #[test]
    fn accepts_builder_canonical_fixture() {
        let fixture = include_bytes!("../tests/fixtures/builder-canonical-sample.json");
        let parsed = parse(fixture).expect("builder canonical bytes");
        assert_eq!(format!("{}\n", encode(&parsed)).as_bytes(), fixture);
    }

    #[test]
    fn rejects_duplicate_key_and_noncanonical_forms() {
        assert!(parse(b"{\"a\":1,\"a\":2}\n").is_err());
        assert!(parse(b"{ \"a\":1}\n").is_err());
        assert!(parse(b"{\"b\":1,\"a\":2}\n").is_err());
        assert!(parse(b"{\"a\":\"\\/\"}\n").is_err());
        assert!(parse(b"{\"a\":01}\n").is_err());
        assert!(parse(b"{\"a\":-0}\n").is_err());
        assert!(parse(b"{\"a\":1}").is_err());
        assert!(parse(b"{\"a\":1}\n\n").is_err());
    }
}
