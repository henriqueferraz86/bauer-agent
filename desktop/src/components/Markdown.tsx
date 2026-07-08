import React from "react";

// Renderer de markdown minimalista e sem dependências para as respostas do
// Bauer no chat. Gera elementos React (nunca dangerouslySetInnerHTML), então
// qualquer HTML vindo do modelo é tratado como texto — sem risco de XSS.
// Suporta o subconjunto que os modelos realmente usam: headings, **bold**,
// *italic*, `code`, blocos ```fence```, listas, tabelas, hr e links http(s).

// Fonte do regex inline — instanciado POR CHAMADA em renderInline: um regex
// global compartilhado teria o lastIndex corrompido pela recursão (bold chama
// renderInline de novo), travando o exec num loop infinito.
const INLINE_SRC =
  "(`[^`\\n]+`)|(\\*\\*[^*\\n]+\\*\\*)|(\\*[^*\\n]+\\*)|(\\[([^\\]\\n]+)\\]\\((https?:\\/\\/[^)\\s]+)\\))";

function renderInline(text: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = new RegExp(INLINE_SRC, "g");
  let last = 0;
  let k = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<code key={k++}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={k++}>{renderInline(m[2].slice(2, -2))}</strong>);
    else if (m[3]) out.push(<em key={k++}>{m[3].slice(1, -1)}</em>);
    else if (m[4])
      out.push(
        <a key={k++} href={m[6]} target="_blank" rel="noreferrer">
          {m[5]}
        </a>
      );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderLines(lines: string[]): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  lines.forEach((ln, i) => {
    if (i > 0) out.push(<br key={`br${i}`} />);
    out.push(<React.Fragment key={i}>{renderInline(ln)}</React.Fragment>);
  });
  return out;
}

const TABLE_SEP_RE = /^\s*\|?[\s:|-]+\|?\s*$/;

function splitRow(row: string): string[] {
  return row.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
}

export default function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const ln = lines[i];

    // Bloco de código cercado — durante streaming a cerca de fechamento pode
    // ainda não ter chegado; renderiza o que houver até o fim.
    const fence = ln.match(/^\s*```(\w*)/);
    if (fence) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++; // pula a cerca de fechamento (ou passa do fim)
      blocks.push(
        <pre key={key++} className="md-pre">
          <code>{code.join("\n")}</code>
        </pre>
      );
      continue;
    }

    const heading = ln.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const lvl = Math.min(heading[1].length, 4);
      blocks.push(
        <div key={key++} className={`md-h md-h${lvl}`}>
          {renderInline(heading[2])}
        </div>
      );
      i++;
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(ln)) {
      blocks.push(<hr key={key++} className="md-hr" />);
      i++;
      continue;
    }

    // Tabela: linha com | seguida da linha separadora |---|---|
    if (ln.includes("|") && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1]) && lines[i + 1].includes("|")) {
      const header = splitRow(ln);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(splitRow(lines[i++]));
      }
      blocks.push(
        <div key={key++} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>{header.map((h, j) => <th key={j}>{renderInline(h)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>{r.map((c, cj) => <td key={cj}>{renderInline(c)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    const li = ln.match(/^(\s*)([-*•]|\d+[.)])\s+(.*)$/);
    if (li) {
      const ordered = /\d/.test(li[2]);
      const items: string[] = [];
      while (i < lines.length) {
        const it = lines[i].match(/^(\s*)([-*•]|\d+[.)])\s+(.*)$/);
        if (!it || /\d/.test(it[2]) !== ordered) break;
        items.push(it[3]);
        i++;
      }
      const children = items.map((t, j) => <li key={j}>{renderInline(t)}</li>);
      blocks.push(
        ordered ? (
          <ol key={key++} className="md-list">{children}</ol>
        ) : (
          <ul key={key++} className="md-list">{children}</ul>
        )
      );
      continue;
    }

    if (ln.trim() === "") {
      i++;
      continue;
    }

    // Parágrafo: agrupa linhas consecutivas que não iniciam outro bloco.
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^\s*```/.test(lines[i]) &&
      !/^#{1,6}\s/.test(lines[i]) &&
      !/^(\s*)([-*•]|\d+[.)])\s+/.test(lines[i]) &&
      !(lines[i].includes("|") && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1]) && lines[i + 1].includes("|"))
    ) {
      para.push(lines[i++]);
    }
    if (para.length === 0) para.push(lines[i++]); // nunca deixar i parado
    blocks.push(
      <p key={key++} className="md-p">
        {renderLines(para)}
      </p>
    );
  }

  return <div className="md">{blocks}</div>;
}
