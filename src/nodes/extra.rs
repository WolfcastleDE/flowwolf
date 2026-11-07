//! More n8n core nodes: XML, Markdown, Compression, Execution Data, Debug Helper.

use super::data::{binary_bytes, make_binary, mime_for};
use super::{Ctx, Out};
use crate::model::*;
use anyhow::{Result, anyhow};
use serde_json::{Map, Value, json};
use std::io::{Read, Write};

pub async fn run(ctx: &Ctx<'_>, kind: &str) -> Option<Result<Out>> {
    Some(match kind {
        "n8n-nodes-base.xml" => xml(ctx),
        "n8n-nodes-base.markdown" => markdown(ctx),
        "n8n-nodes-base.compression" => compression(ctx),
        "n8n-nodes-base.executionData" => execution_data(ctx),
        "n8n-nodes-base.debugHelper" => debug_helper(ctx),
        _ => return None,
    })
}

// ---------------------------------------------------------------------------
// XML <-> JSON (xml2js conventions: attributes under "$", text under "_")
// ---------------------------------------------------------------------------

fn add_child(map: &mut Map<String, Value>, key: String, v: Value) {
    match map.get_mut(&key) {
        Some(Value::Array(a)) => a.push(v),
        Some(prev) => *prev = json!([prev.clone(), v]),
        None => {
            map.insert(key, v);
        }
    }
}

pub fn xml_to_json(s: &str) -> Result<Value> {
    use quick_xml::events::Event;
    let mut reader = quick_xml::Reader::from_str(s);
    reader.config_mut().trim_text(true);
    // (element name, children/attributes, text)
    let mut stack: Vec<(String, Map<String, Value>, String)> = vec![("#root".into(), Map::new(), String::new())];
    let open = |e: &quick_xml::events::BytesStart| -> Result<(String, Map<String, Value>, String)> {
        let name = String::from_utf8_lossy(e.name().as_ref()).into_owned();
        let mut m = Map::new();
        let mut attrs = Map::new();
        for a in e.attributes() {
            let a = a?;
            attrs.insert(String::from_utf8_lossy(a.key.as_ref()).into_owned(), json!(a.unescape_value()?.into_owned()));
        }
        if !attrs.is_empty() {
            m.insert("$".into(), Value::Object(attrs));
        }
        Ok((name, m, String::new()))
    };
    let close = |(name, mut m, text): (String, Map<String, Value>, String), parent: &mut (String, Map<String, Value>, String)| {
        let v = if m.is_empty() {
            json!(text)
        } else {
            if !text.is_empty() {
                m.insert("_".into(), json!(text));
            }
            Value::Object(m)
        };
        add_child(&mut parent.1, name, v);
    };
    loop {
        match reader.read_event()? {
            Event::Start(e) => stack.push(open(&e)?),
            Event::Empty(e) => {
                let el = open(&e)?;
                close(el, stack.last_mut().unwrap());
            }
            Event::End(_) => {
                let el = stack.pop().ok_or_else(|| anyhow!("Unbalanced XML"))?;
                close(el, stack.last_mut().ok_or_else(|| anyhow!("Unbalanced XML"))?);
            }
            Event::Text(t) => stack.last_mut().unwrap().2.push_str(&t.unescape()?),
            Event::CData(t) => stack.last_mut().unwrap().2.push_str(&String::from_utf8_lossy(&t)),
            Event::Eof => break,
            _ => {}
        }
    }
    if stack.len() != 1 {
        return Err(anyhow!("Unclosed XML element"));
    }
    Ok(Value::Object(stack.pop().unwrap().1))
}

fn esc_xml(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

fn write_xml(name: &str, v: &Value, out: &mut String) {
    match v {
        Value::Array(a) => a.iter().for_each(|x| write_xml(name, x, out)),
        Value::Object(m) => {
            out.push('<');
            out.push_str(name);
            for (k, x) in m.get("$").and_then(|a| a.as_object()).into_iter().flatten() {
                out.push_str(&format!(" {k}=\"{}\"", esc_xml(&as_str(x))));
            }
            out.push('>');
            if let Some(t) = m.get("_") {
                out.push_str(&esc_xml(&as_str(t)));
            }
            for (k, x) in m.iter().filter(|(k, _)| *k != "$" && *k != "_") {
                write_xml(k, x, out);
            }
            out.push_str(&format!("</{name}>"));
        }
        Value::Null => out.push_str(&format!("<{name}/>")),
        other => out.push_str(&format!("<{name}>{}</{name}>", esc_xml(&as_str(other)))),
    }
}

pub fn json_to_xml(v: &Value, root: &str, headless: bool) -> String {
    let mut out = if headless { String::new() } else { "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n".into() };
    // A single top-level key becomes the root element, like xml2js.
    match v.as_object() {
        Some(m) if m.len() == 1 && !m.contains_key("$") => {
            let (k, x) = m.iter().next().unwrap();
            write_xml(k, x, &mut out);
        }
        _ => write_xml(root, v, &mut out),
    }
    out
}

fn xml(ctx: &Ctx) -> Result<Out> {
    let mode = ctx.s("mode", 0, "xmlToJson")?;
    let prop = ctx.s("dataPropertyName", 0, "data")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let j = if mode == "jsonToXml" {
            let x = json_to_xml(&it.json, &ctx.s("options.rootName", i, "root")?, ctx.b("options.headless", i, false)?);
            json!({ prop.clone(): x })
        } else {
            let src = get_path(&it.json, &prop).map(as_str).ok_or_else(|| anyhow!("Item has no JSON property called '{prop}'"))?;
            let mut v = xml_to_json(&src)?;
            if let (Value::Object(o), Value::Object(orig)) = (&mut v, &it.json) {
                for (k, x) in orig.iter().filter(|(k, _)| **k != prop) {
                    o.entry(k.clone()).or_insert(x.clone());
                }
            }
            v
        };
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// Markdown <-> HTML
// ---------------------------------------------------------------------------

pub fn md_to_html(md: &str) -> String {
    let parser = pulldown_cmark::Parser::new_ext(md, pulldown_cmark::Options::all());
    let mut out = String::new();
    pulldown_cmark::html::push_html(&mut out, parser);
    out
}

fn html_node_to_md(node: ego_tree::NodeRef<scraper::Node>, out: &mut String, list: &mut Vec<(bool, usize)>) {
    use scraper::Node;
    match node.value() {
        Node::Text(t) => out.push_str(&t.replace('\n', " ")),
        Node::Element(e) => {
            let tag = e.name();
            let inner = |out: &mut String, list: &mut Vec<(bool, usize)>| node.children().for_each(|c| html_node_to_md(c, out, list));
            match tag {
                "h1" | "h2" | "h3" | "h4" | "h5" | "h6" => {
                    out.push_str(&format!("\n\n{} ", "#".repeat(tag[1..].parse().unwrap_or(1))));
                    inner(out, list);
                    out.push_str("\n\n");
                }
                "p" | "div" => {
                    out.push_str("\n\n");
                    inner(out, list);
                    out.push_str("\n\n");
                }
                "br" => out.push_str("  \n"),
                "strong" | "b" => {
                    out.push_str("**");
                    inner(out, list);
                    out.push_str("**");
                }
                "em" | "i" => {
                    out.push('*');
                    inner(out, list);
                    out.push('*');
                }
                "code" => {
                    out.push('`');
                    inner(out, list);
                    out.push('`');
                }
                "pre" => {
                    let text: String = scraper::ElementRef::wrap(node).map(|e| e.text().collect()).unwrap_or_default();
                    out.push_str(&format!("\n\n```\n{}\n```\n\n", text.trim_end()));
                }
                "a" => {
                    out.push('[');
                    inner(out, list);
                    out.push_str(&format!("]({})", e.attr("href").unwrap_or("")));
                }
                "img" => out.push_str(&format!("![{}]({})", e.attr("alt").unwrap_or(""), e.attr("src").unwrap_or(""))),
                "ul" | "ol" => {
                    list.push((tag == "ol", 0));
                    out.push('\n');
                    inner(out, list);
                    list.pop();
                    out.push('\n');
                }
                "li" => {
                    let depth = list.len().saturating_sub(1);
                    let marker = match list.last_mut() {
                        Some((true, n)) => {
                            *n += 1;
                            format!("{n}.")
                        }
                        _ => "-".into(),
                    };
                    out.push_str(&format!("\n{}{marker} ", "  ".repeat(depth)));
                    inner(out, list);
                }
                "blockquote" => {
                    let mut q = String::new();
                    inner(&mut q, list);
                    out.push_str(&format!("\n\n{}\n\n", q.trim().lines().map(|l| format!("> {l}")).collect::<Vec<_>>().join("\n")));
                }
                "hr" => out.push_str("\n\n---\n\n"),
                "script" | "style" | "head" => {}
                _ => inner(out, list),
            }
        }
        _ => node.children().for_each(|c| html_node_to_md(c, out, list)),
    }
}

pub fn html_to_md(html: &str) -> String {
    let doc = scraper::Html::parse_fragment(html);
    let mut out = String::new();
    html_node_to_md(doc.tree.root(), &mut out, &mut vec![]);
    // Collapse the blank lines produced by nested blocks.
    let mut res = String::new();
    let mut blank = 0;
    for l in out.lines().map(|l| l.trim_end()) {
        if l.is_empty() {
            blank += 1;
            if blank > 1 {
                continue;
            }
        } else {
            blank = 0;
        }
        res.push_str(l);
        res.push('\n');
    }
    res.trim().to_string()
}

fn markdown(ctx: &Ctx) -> Result<Out> {
    let mode = ctx.s("mode", 0, "htmlToMarkdown")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let dest = ctx.s("destinationKey", i, "data")?;
        let v = if mode == "markdownToHtml" { md_to_html(&ctx.s("markdown", i, "")?) } else { html_to_md(&ctx.s("html", i, "")?) };
        let mut j = it.json.clone();
        set_path(&mut j, &dest, json!(v));
        out.push(Item::paired(j, i));
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// Compression (zip / gzip)
// ---------------------------------------------------------------------------

fn compression(ctx: &Ctx) -> Result<Out> {
    let op = ctx.s("operation", 0, "compress")?;
    let mut out = vec![];
    for (i, it) in ctx.items().iter().enumerate() {
        let props: Vec<String> = ctx.s("binaryPropertyName", i, "data")?.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
        let bins = it.binary.clone().unwrap_or_default();
        let mut result = Map::new();
        if op == "compress" {
            let format = ctx.s("outputFormat", i, "zip")?;
            if format == "gzip" {
                for p in &props {
                    let b = bins.get(p).ok_or_else(|| anyhow!("No binary property \"{p}\" [item {i}]"))?;
                    let mut enc = flate2::write::GzEncoder::new(vec![], flate2::Compression::default());
                    enc.write_all(&binary_bytes(b)?)?;
                    let name = format!("{}.gz", b["fileName"].as_str().unwrap_or("data"));
                    result.insert(p.clone(), make_binary(&enc.finish()?, "application/gzip", &name));
                }
            } else {
                let mut zw = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
                let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
                for p in &props {
                    let b = bins.get(p).ok_or_else(|| anyhow!("No binary property \"{p}\" [item {i}]"))?;
                    zw.start_file(b["fileName"].as_str().unwrap_or(p.as_str()), opts)?;
                    zw.write_all(&binary_bytes(b)?)?;
                }
                let bytes = zw.finish()?.into_inner();
                let name = ctx.s("fileName", i, "data.zip")?;
                result.insert(ctx.s("binaryPropertyOutput", i, "data")?, make_binary(&bytes, "application/zip", &name));
            }
        } else {
            let prefix = ctx.s("outputPrefix", i, "file_")?;
            let mut n = 0;
            for p in &props {
                let b = bins.get(p).ok_or_else(|| anyhow!("No binary property \"{p}\" [item {i}]"))?;
                let bytes = binary_bytes(b)?;
                if bytes.starts_with(&[0x1f, 0x8b]) {
                    let mut s = vec![];
                    flate2::read::GzDecoder::new(bytes.as_slice()).read_to_end(&mut s)?;
                    let name = b["fileName"].as_str().unwrap_or("data").trim_end_matches(".gz").to_string();
                    result.insert(format!("{prefix}{n}"), make_binary(&s, mime_for(&name), &name));
                    n += 1;
                } else {
                    let mut za = zip::ZipArchive::new(std::io::Cursor::new(bytes))?;
                    for k in 0..za.len() {
                        let mut f = za.by_index(k)?;
                        if f.is_dir() {
                            continue;
                        }
                        let name = f.name().rsplit('/').next().unwrap_or("file").to_string();
                        let mut s = vec![];
                        f.read_to_end(&mut s)?;
                        result.insert(format!("{prefix}{n}"), make_binary(&s, mime_for(&name), &name));
                        n += 1;
                    }
                }
            }
        }
        out.push(Item { json: it.json.clone(), binary: Some(result), paired_item: Some(json!({"item": i})) });
    }
    Ok(vec![out])
}

// ---------------------------------------------------------------------------
// Execution Data & Debug Helper
// ---------------------------------------------------------------------------

fn execution_data(ctx: &Ctx) -> Result<Out> {
    for (i, _) in ctx.items().iter().enumerate().take(1) {
        let mut cd = ctx.eng.custom_data.borrow_mut();
        for e in ctx.list("dataToSave.values", i)? {
            let k = as_str(&e["key"]);
            if !k.is_empty() {
                cd.insert(k, json!(as_str(&e["value"]).chars().take(255).collect::<String>()));
            }
        }
    }
    Ok(vec![ctx.input(0)])
}

fn debug_helper(ctx: &Ctx) -> Result<Out> {
    match ctx.s("category", 0, "doNothing")?.as_str() {
        "throwError" => Err(anyhow!("{}", ctx.s("throwErrorMessage", 0, "Node has thrown an error")?)),
        "randomData" => {
            let kind = ctx.s("randomDataType", 0, "user")?;
            let count = ctx.n("randomDataCount", 0, 10.0)?.clamp(1.0, 10_000.0) as usize;
            const FIRST: [&str; 8] = ["Ada", "Linus", "Grace", "Alan", "Margaret", "Ken", "Barbara", "Dennis"];
            const LAST: [&str; 8] = ["Lovelace", "Torvalds", "Hopper", "Turing", "Hamilton", "Thompson", "Liskov", "Ritchie"];
            Ok(vec![(0..count)
                .map(|n| {
                    let r = uuid::Uuid::new_v4();
                    let b = r.as_bytes();
                    let (f, l) = (FIRST[b[0] as usize % 8], LAST[b[1] as usize % 8]);
                    Item::new(match kind.as_str() {
                        "uuid" => json!({"uuid": r.to_string()}),
                        "email" => json!({"email": format!("{}.{}{}@example.com", f.to_lowercase(), l.to_lowercase(), b[2])}),
                        "number" => json!({"number": u32::from_le_bytes([b[3], b[4], b[5], b[6]]) % 1_000_000}),
                        _ => json!({"id": n + 1, "uuid": r.to_string(), "firstname": f, "lastname": l,
                                    "email": format!("{}.{}@example.com", f.to_lowercase(), l.to_lowercase()), "age": 18 + b[7] % 60}),
                    })
                })
                .collect()])
        }
        _ => Ok(vec![ctx.input(0)]),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xml_markdown() {
        let x = r#"<?xml version="1.0"?><order id="7"><item sku="a">Pen</item><item sku="b">Ink</item><note>fast &amp; safe</note><gift/></order>"#;
        let v = xml_to_json(x).unwrap();
        assert_eq!(v["order"]["$"]["id"], json!("7"));
        assert_eq!(v["order"]["item"][1], json!({"$": {"sku": "b"}, "_": "Ink"}));
        assert_eq!(v["order"]["note"], json!("fast & safe"));
        let back = json_to_xml(&v, "root", true);
        assert_eq!(xml_to_json(&back).unwrap(), v);
        assert!(md_to_html("# Hi\n\n**bold** [x](https://e.x)").contains("<h1>Hi</h1>"));
        assert_eq!(html_to_md("<h2>Title</h2><p>Some <b>bold</b> and <a href=\"/x\">link</a></p><ul><li>one</li><li>two</li></ul>"),
                   "## Title\n\nSome **bold** and [link](/x)\n\n- one\n- two");
    }
}
