//! SPIKE: the GQ 3 red-team runner (docs/spikes/gq3-redteam.md).
//!
//! Parses every `.gq` file under `docs/spikes/gq3-redteam/corpus/` as
//! edition 3 and compares the outcome with the file's `// expect:` header.
//! For every refusal it also plays the agent: it takes the printed fix,
//! applies it literally over the construct the diagnostic points at, parses
//! the result, and, when the header names the fix it expected, compares the
//! two repaired texts' trees. The one-spelling pairs under `pairs/` are
//! parsed both ways and reported as the same tree or two trees.
//!
//! `redteam_corpus_matches_its_pinned_mismatches` passes while the set of
//! mismatches equals `known-mismatches.txt`, so the corpus stays an owner of
//! the parser's current behaviour. `redteam_corpus_strict` is the instrument:
//! it fails on any mismatch. Set `GQ3_REDTEAM_OUT=<dir>` to write the table
//! and the fix audit there.

use std::collections::BTreeSet;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

use pest::Parser;
use pest::iterators::Pair;

use super::ast3::*;
use super::diagnostic::QueryDiagnostic;
use super::parser::{QueryParser, Rule, parse_query_diagnostic};
use super::parser3::{EditionLine, edition_line, parse_query3};
use super::print3::{self, Style};

/// The RFC's section 8 schema in today's `.pg` spelling (`implements`, no
/// analyzer or distance options), with `SourceRevision` and `Passage` for the
/// section 6 example.
const SCHEMA: &str = r#"
interface Named { name: String }
node Person implements Named { handle: String @key  name: String  age: I32?  city: String? }
node Company implements Named { slug: String @key  name: String }
node Doc { id: String @key  title: String @index  body: String @index  year: I32  tags: [String]  embedding: Vector(3)? }
node Collection { slug: String @key  min_docs: I32 }
node SourceRevision { path: String @key  matter_number: String }
node Passage { locator: String @key  text: String @index }
edge WorksAt: Person -> Company @card(0..1)
edge Knows: Person -> Person
edge Authored: Person -> Doc
edge Cites: Doc -> Doc
edge In: Doc -> Collection
edge HasPassage: SourceRevision -> Passage
"#;

/// What the type checker says about a text the front door lowers: `ok`, or
/// the first refusal's code and message.
fn typecheck(text: &str) -> String {
    use crate::catalog::build_catalog;
    use crate::schema::parser::parse_schema;
    let catalog = build_catalog(&parse_schema(SCHEMA).unwrap()).unwrap();
    let Ok(file) = parse_query_diagnostic(text) else {
        return String::new();
    };
    let crate::query::ast::FileBody::Queries(decls) = &file.body else {
        return "ok".to_string();
    };
    for d in decls {
        if let Err(e) = super::typecheck::typecheck_query_decl(&catalog, d) {
            return match e.diagnostic() {
                Some(d) => format!("{} {}", d.code.as_str(), d.message),
                None => e.to_string(),
            };
        }
    }
    "ok".to_string()
}

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../docs/spikes/gq3-redteam")
}

#[derive(Debug, Clone, PartialEq)]
enum Expect {
    Accept,
    Refuse,
    RefuseWithFix(String),
}

#[derive(Debug, Clone)]
struct Case {
    name: String,
    kind: String,
    habit: Option<String>,
    expect: Expect,
    shape: Option<String>,
    returns: Option<String>,
    later: Option<String>,
    text: String,
}

fn header(content: &str, key: &str) -> Option<String> {
    let prefix = format!("// {key}: ");
    content
        .lines()
        .take_while(|l| l.starts_with("//"))
        .find_map(|l| l.strip_prefix(&prefix).map(|v| v.trim_end().to_string()))
}

fn load_case(path: &Path) -> Case {
    let content = std::fs::read_to_string(path).unwrap();
    let expect = header(&content, "expect").unwrap_or_else(|| panic!("{path:?}: no expect"));
    let expect = match expect.as_str() {
        "accept" => Expect::Accept,
        "refuse" => Expect::Refuse,
        other => Expect::RefuseWithFix(
            other
                .strip_prefix("refuse-with-fix:")
                .unwrap_or_else(|| panic!("{path:?}: bad expect {other}"))
                .replace("\\n", "\n"),
        ),
    };
    let raw = header(&content, "raw").as_deref() == Some("yes");
    let text = if raw {
        content.clone()
    } else {
        format!("gq 3;\n{content}")
    };
    Case {
        name: path.file_stem().unwrap().to_string_lossy().into_owned(),
        kind: header(&content, "kind").unwrap(),
        habit: header(&content, "habit"),
        expect,
        shape: header(&content, "shape"),
        returns: header(&content, "returns"),
        later: header(&content, "later"),
        text,
    }
}

fn files(dir: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|e| e == "gq"))
        .collect();
    out.sort();
    out
}

/// Parse-level outcome: edition 3 through its own parser; any other edition
/// line (or none, for a raw file) through the front door.
fn parse(text: &str) -> Result<Option<File3>, QueryDiagnostic> {
    match edition_line(text) {
        EditionLine::Present { edition: 3, .. } => parse_query3(text).map(Some),
        _ => parse_query_diagnostic(text).map(|_| None),
    }
}

fn norm(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn first_decl(file: &File3) -> Option<&Decl3> {
    match &file.body {
        Body3::Decls(d) => d.last(),
        Body3::Explain(d) => Some(d),
        Body3::Statement(_) => None,
    }
}

fn shape_of(file: &File3) -> (String, String) {
    let style = Style::default();
    let Some(d) = first_decl(file) else {
        return (String::new(), String::new());
    };
    match &d.body {
        DeclBody3::Read(r) => (
            r.clauses
                .iter()
                .map(|c| print3::clause(c, style))
                .collect::<Vec<_>>()
                .join(" | "),
            r.projections
                .iter()
                .map(|p| match &p.alias {
                    Some(a) => format!("{} as {a}", print3::expr(&p.expr)),
                    None => print3::expr(&p.expr),
                })
                .collect::<Vec<_>>()
                .join(" | "),
        ),
        DeclBody3::Write(w) => (
            w.clauses
                .iter()
                .map(|c| print3::clause(c, style))
                .collect::<Vec<_>>()
                .join(" | "),
            String::new(),
        ),
    }
}

/// The span of the construct a diagnostic points at, found the way an agent
/// reads it: the innermost recognised construct that contains the position.
fn construct_span(text: &str, d: &QueryDiagnostic) -> Option<(usize, usize)> {
    let byte = d.position.as_ref()?.byte as usize;
    if d.code.as_str() == "Q006" {
        if let EditionLine::Present { start, end, .. } = edition_line(text) {
            return Some((start, end));
        }
    }
    let rules: &[Rule] = match d.code.as_str() {
        "Q002" => &[Rule::missing_param_list3],
        "Q007" => &[Rule::arrow_traversal],
        "Q008" => &[Rule::where_stmt],
        "Q009" => &[Rule::mutation_decl],
        "Q010" => &[Rule::binding_brace_filter],
        "Q011" => &[Rule::as_commit_clause, Rule::as_unattached_clause],
        "Q012" => &[
            Rule::comparison3,
            Rule::sum,
            Rule::list_to_rows,
            Rule::case_keyword,
            Rule::annotation,
        ],
        "Q013" => &[Rule::clause_after_stmt],
        _ => return None,
    };
    let pairs = QueryParser::parse(Rule::gq3_file, text).ok()?;
    let mut best: Option<Pair<Rule>> = None;
    for p in pairs.flatten() {
        let s = p.as_span();
        if rules.contains(&p.as_rule()) && s.start() <= byte && byte < s.end().max(s.start() + 1) {
            let narrower = best
                .as_ref()
                .is_none_or(|b| s.end() - s.start() < b.as_span().end() - b.as_span().start());
            // For `||` the innermost `sum` holding the operator is wanted, but
            // for an infix `contains` the comparison holds it, not a sum.
            if narrower {
                best = Some(p);
            }
        }
    }
    let p = best?;
    if p.as_rule() == Rule::missing_param_list3 {
        let ident = p
            .clone()
            .into_inner()
            .find(|i| i.as_rule() == Rule::ident)?;
        return Some((p.as_span().start(), ident.as_span().end()));
    }
    Some((p.as_span().start(), p.as_span().end()))
}

fn splice(text: &str, span: (usize, usize), with: &str) -> String {
    format!("{}{}{}", &text[..span.0], with, &text[span.1..])
}

#[derive(Debug, Clone, PartialEq)]
enum FixVerdict {
    /// The diagnostic printed no fix.
    NoFix,
    /// A fix was printed, but no construct span could be found to apply it to.
    NoSpan,
    /// Applied literally, the text does not parse.
    DoesNotParse(String),
    /// It parses, and its tree differs from the tree of the expected fix.
    ChangesMeaning,
    /// It parses, and either matches the expected fix's tree or no expected
    /// fix was given.
    Parses,
}

struct Row {
    case: Case,
    actual: Result<(), QueryDiagnostic>,
    pipeline: String,
    mismatch: Option<String>,
    fix: Option<FixVerdict>,
    applied: Option<String>,
}

fn verdict_name(v: &FixVerdict) -> String {
    match v {
        FixVerdict::NoFix => "no fix".into(),
        FixVerdict::NoSpan => "fix, no span".into(),
        FixVerdict::DoesNotParse(c) => format!("fix does not parse ({c})"),
        FixVerdict::ChangesMeaning => "fix changes meaning".into(),
        FixVerdict::Parses => "fix parses".into(),
    }
}

fn audit_fix(case: &Case, d: &QueryDiagnostic) -> (FixVerdict, Option<String>) {
    // A machine-applicable suggestion is what a tool applies first.
    let repaired = if let Some(s) = &d.suggestion {
        let mut t = case.text.clone();
        let mut edits = s.edits.clone();
        edits.sort_by_key(|e| std::cmp::Reverse(e.start));
        for e in edits {
            t.replace_range(e.start..e.end, &e.replacement);
        }
        Some(t)
    } else {
        None
    };
    let span = construct_span(&case.text, d);
    let repaired = match (repaired, &d.fix, span) {
        (Some(t), _, _) => t,
        (None, None, _) => return (FixVerdict::NoFix, None),
        (None, Some(_), None) => return (FixVerdict::NoSpan, None),
        (None, Some(fix), Some(span)) => splice(&case.text, span, fix),
    };
    let got = match parse(&repaired) {
        Err(e) => {
            return (
                FixVerdict::DoesNotParse(e.code.as_str().to_string()),
                Some(repaired),
            );
        }
        Ok(f) => f,
    };
    if let (Expect::RefuseWithFix(expected), Some(span)) = (&case.expect, span) {
        let with = if expected == "<empty>" {
            ""
        } else {
            expected.as_str()
        };
        if let Ok(want) = parse(&splice(&case.text, span, with)) {
            if want != got {
                return (FixVerdict::ChangesMeaning, Some(repaired));
            }
        }
    }
    (FixVerdict::Parses, Some(repaired))
}

fn run_case(case: Case) -> Row {
    let parsed = parse(&case.text);
    let pipeline = match parse_query_diagnostic(&case.text) {
        Ok(_) => format!("lowers; typecheck: {}", typecheck(&case.text)),
        Err(e) => format!("{} {}", e.code.as_str(), e.message),
    };
    let mut mismatch = None;
    let mut fix = None;
    let mut applied = None;
    match (&case.expect, &parsed) {
        (Expect::Accept, Ok(file)) => {
            if let Some(file) = file {
                let (clauses, returns) = shape_of(file);
                if let Some(want) = &case.shape {
                    if norm(want) != norm(&clauses) {
                        mismatch = Some(format!("shape: expected `{want}`, parsed `{clauses}`"));
                    }
                }
                if let Some(want) = &case.returns {
                    if norm(want) != norm(&returns) {
                        mismatch = Some(format!("returns: expected `{want}`, parsed `{returns}`"));
                    }
                }
            }
        }
        (Expect::Accept, Err(e)) => {
            mismatch = Some(format!(
                "refused, expected accept: {} {}",
                e.code.as_str(),
                e.message
            ))
        }
        (Expect::Refuse | Expect::RefuseWithFix(_), Ok(file)) => {
            let (clauses, returns) = file.as_ref().map(shape_of).unwrap_or_default();
            mismatch = Some(format!(
                "accepted, expected a refusal; parsed `{clauses}` returning `{returns}`"
            ));
        }
        (Expect::Refuse, Err(_)) => {}
        (Expect::RefuseWithFix(want), Err(e)) => match &e.fix {
            Some(f) if norm(f) == norm(want) => {}
            Some(f) => mismatch = Some(format!("fix differs: printed `{f}`")),
            None => mismatch = Some("no fix printed".to_string()),
        },
    }
    if let Err(d) = &parsed {
        let (v, t) = audit_fix(&case, d);
        fix = Some(v);
        applied = t;
    }
    Row {
        actual: parsed.map(|_| ()),
        case,
        pipeline,
        mismatch,
        fix,
        applied,
    }
}

fn md_cell(s: &str) -> String {
    s.replace('|', "\\|").replace('\n', "\\n")
}

struct Report {
    rows: Vec<Row>,
    table: String,
    fixes: String,
    pairs: String,
}

fn run_all() -> Report {
    let rows: Vec<Row> = files(&root().join("corpus"))
        .iter()
        .map(|p| run_case(load_case(p)))
        .collect();

    let mut table = String::from(
        "| case | kind | habit | expect | parse | code | message | printed fix | fix applied | front door | verdict |\n|---|---|---|---|---|---|---|---|---|---|---|\n",
    );
    for r in &rows {
        let expect = match &r.case.expect {
            Expect::Accept => "accept".to_string(),
            Expect::Refuse => "refuse".to_string(),
            Expect::RefuseWithFix(f) => format!("fix: `{}`", md_cell(f)),
        };
        let (parse, code, message, printed) = match &r.actual {
            Ok(()) => ("accept", String::new(), String::new(), String::new()),
            Err(e) => (
                "refuse",
                e.code.as_str().to_string(),
                md_cell(&e.message),
                e.fix.as_deref().map(md_cell).unwrap_or_default(),
            ),
        };
        let verdict = match &r.mismatch {
            None => "ok".to_string(),
            Some(m) => format!("**MISMATCH**: {}", md_cell(m)),
        };
        let later = r
            .case
            .later
            .as_deref()
            .map(|l| format!(" (RFC later: {})", md_cell(l)))
            .unwrap_or_default();
        let _ = writeln!(
            table,
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {}{} | {} |",
            r.case.name,
            r.case.kind,
            r.case.habit.as_deref().unwrap_or(""),
            expect,
            parse,
            code,
            message,
            if printed.is_empty() {
                String::new()
            } else {
                format!("`{printed}`")
            },
            r.fix.as_ref().map(verdict_name).unwrap_or_default(),
            md_cell(&r.pipeline),
            later,
            verdict,
        );
    }

    // The fix audit: every refusal, counted by verdict.
    let refusals: Vec<&Row> = rows.iter().filter(|r| r.actual.is_err()).collect();
    let count = |f: &dyn Fn(&FixVerdict) -> bool| {
        refusals
            .iter()
            .filter(|r| r.fix.as_ref().is_some_and(f))
            .count()
    };
    let generic = refusals
        .iter()
        .filter(|r| matches!(&r.actual, Err(e) if e.code.as_str() == "Q001"))
        .count();
    let mut fixes = String::new();
    let _ = writeln!(fixes, "refusals: {}", refusals.len());
    let _ = writeln!(
        fixes,
        "  generic Q001 (grammar rule names, no fix): {generic}"
    );
    let _ = writeln!(
        fixes,
        "  no fix printed: {}",
        count(&|v| *v == FixVerdict::NoFix)
    );
    let _ = writeln!(
        fixes,
        "  fix printed: {}",
        count(&|v| *v != FixVerdict::NoFix)
    );
    let _ = writeln!(
        fixes,
        "    applied literally, parses with the expected meaning: {}",
        count(&|v| *v == FixVerdict::Parses)
    );
    let _ = writeln!(
        fixes,
        "    applied literally, parses but changes meaning: {}",
        count(&|v| *v == FixVerdict::ChangesMeaning)
    );
    let _ = writeln!(
        fixes,
        "    applied literally, does not parse: {}",
        count(&|v| matches!(v, FixVerdict::DoesNotParse(_)))
    );
    let _ = writeln!(
        fixes,
        "    no construct span to apply it to: {}",
        count(&|v| *v == FixVerdict::NoSpan)
    );
    let _ = writeln!(fixes);
    for r in &refusals {
        let Err(e) = &r.actual else { continue };
        if let Some(v) = &r.fix {
            if matches!(
                v,
                FixVerdict::DoesNotParse(_) | FixVerdict::ChangesMeaning | FixVerdict::NoSpan
            ) {
                let _ = writeln!(
                    fixes,
                    "{}: {} `{}`\n  fix: {:?}\n  verdict: {}\n  repaired: {}\n",
                    r.case.name,
                    e.code.as_str(),
                    e.message,
                    e.fix,
                    verdict_name(v),
                    r.applied
                        .as_deref()
                        .map(|t| norm(t.trim_start_matches("gq 3;")))
                        .unwrap_or_default()
                );
            }
        }
    }

    // One-spelling pairs.
    let mut pairs = String::from("| pair | covered | trees |\n|---|---|---|\n");
    for p in files(&root().join("pairs")) {
        let content = std::fs::read_to_string(&p).unwrap();
        let (a, b) = content
            .split_once("\n// ---\n")
            .expect("a pair has two texts");
        let trees = match (parse(&format!("gq 3;\n{a}")), parse(&format!("gq 3;\n{b}"))) {
            (Ok(x), Ok(y)) if x == y => "one tree".to_string(),
            (Ok(_), Ok(_)) => "two trees".to_string(),
            (x, y) => format!(
                "refused: {:?} / {:?}",
                x.err().map(|e| e.code.as_str()),
                y.err().map(|e| e.code.as_str())
            ),
        };
        let _ = writeln!(
            pairs,
            "| {} | {} | {} |",
            p.file_stem().unwrap().to_string_lossy(),
            header(&content, "covered").unwrap_or_default(),
            trees
        );
    }

    Report {
        rows,
        table,
        fixes,
        pairs,
    }
}

fn write_out(report: &Report) {
    if let Ok(dir) = std::env::var("GQ3_REDTEAM_OUT") {
        let dir = PathBuf::from(dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("results.md"), &report.table).unwrap();
        std::fs::write(dir.join("fix-audit.txt"), &report.fixes).unwrap();
        std::fs::write(dir.join("pairs.md"), &report.pairs).unwrap();
    }
}

fn mismatches(report: &Report) -> BTreeSet<String> {
    report
        .rows
        .iter()
        .filter(|r| r.mismatch.is_some())
        .map(|r| r.case.name.clone())
        .collect()
}

#[test]
fn redteam_corpus_matches_its_pinned_mismatches() {
    let report = run_all();
    write_out(&report);
    assert!(
        report.rows.len() >= 200,
        "the corpus holds at least 200 cases"
    );
    let known: BTreeSet<String> = std::fs::read_to_string(root().join("known-mismatches.txt"))
        .unwrap()
        .lines()
        .map(|l| l.split('#').next().unwrap().trim().to_string())
        .filter(|l| !l.is_empty())
        .collect();
    let got = mismatches(&report);
    let new: Vec<_> = got.difference(&known).collect();
    let gone: Vec<_> = known.difference(&got).collect();
    assert!(
        new.is_empty() && gone.is_empty(),
        "mismatches moved: new {new:?}, no longer mismatching {gone:?}"
    );
}

#[test]
#[ignore = "instrument: the GQ 3 red-team corpus; fails on any mismatch with its headers"]
fn redteam_corpus_strict() {
    let report = run_all();
    write_out(&report);
    println!("{}", report.table);
    println!("{}", report.fixes);
    println!("{}", report.pairs);
    let got = mismatches(&report);
    println!("{} cases, {} mismatches", report.rows.len(), got.len());
    assert!(got.is_empty(), "{} mismatches: {got:?}", got.len());
}
