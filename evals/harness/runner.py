"""Runner da Harness Evaluation Suite.

Mede **comportamento do harness**, não qualidade de modelo. Todo cenário roda com
executor stub e sem rede: o que está sob teste é o Kernel decidir certo — barrar,
reprovar, cancelar, recuperar — e isso não pode depender de um provider vivo nem
de um modelo estar bem-humorado. É também o que permite rodar em CI.

Por que isto não é "mais um teste unitário": os testes provam que uma peça
funciona; os cenários medem **taxas** ao longo do tempo — `false_success_rate` e
`orphaned_run_rate`, as duas métricas que de fato dizem se um harness governa.
Sem elas o scorecard é opinião. O relatório é comparável entre versões, e é o que
o §15 do plano usa para declarar (ou não) os 90%.

Uso:
    python -m evals.harness                 # roda tudo, imprime tabela
    python -m evals.harness --json out.json # relatório para comparar versões
"""

from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class Veredito:
    nome: str
    passou: bool
    critico: bool = False
    detalhe: str = ""
    #: cenário cuja falha significa "o harness declarou sucesso sem base"
    mede_falso_sucesso: bool = False
    #: cenário cuja falha significa "sobrou run pendurado"
    mede_run_orfao: bool = False
    erro: str = ""


@dataclass
class Relatorio:
    vereditos: list[Veredito] = field(default_factory=list)

    # ── taxas que definem o harness ──────────────────────────────────────────

    @property
    def total(self) -> int:
        return len(self.vereditos)

    @property
    def passaram(self) -> int:
        return sum(1 for v in self.vereditos if v.passou)

    @property
    def taxa_geral(self) -> float:
        return (self.passaram / self.total) if self.total else 0.0

    @property
    def taxa_criticos(self) -> float:
        crit = [v for v in self.vereditos if v.critico]
        return (sum(1 for v in crit if v.passou) / len(crit)) if crit else 1.0

    @property
    def taxa_falso_sucesso(self) -> float:
        """Fração dos cenários de falso sucesso em que o harness FALHOU.

        Falhar aqui é literalmente "concluiu como sucesso o que não era" — a
        métrica mais importante do plano, e a que ninguém conseguia medir antes
        de existir esta suíte.
        """
        alvo = [v for v in self.vereditos if v.mede_falso_sucesso]
        return (sum(1 for v in alvo if not v.passou) / len(alvo)) if alvo else 0.0

    @property
    def taxa_run_orfao(self) -> float:
        alvo = [v for v in self.vereditos if v.mede_run_orfao]
        return (sum(1 for v in alvo if not v.passou) / len(alvo)) if alvo else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cenarios": self.total,
            "passaram": self.passaram,
            "taxa_geral": round(self.taxa_geral, 4),
            "taxa_criticos": round(self.taxa_criticos, 4),
            "false_success_rate": round(self.taxa_falso_sucesso, 4),
            "orphaned_run_rate": round(self.taxa_run_orfao, 4),
            "vereditos": [asdict(v) for v in self.vereditos],
        }


#: registro global — cenários se declaram pelo decorador
_CENARIOS: list[tuple[str, dict[str, Any], Callable[..., Any]]] = []


def cenario(nome: str, *, critico: bool = False, falso_sucesso: bool = False,
            run_orfao: bool = False):
    """Registra um cenário.

    ``critico`` reprova o release inteiro se falhar (§15: critical_eval_pass_rate
    tem de ser 100%). Os demais entram na taxa geral, cujo piso é 90%.
    """
    meta = {"critico": critico, "mede_falso_sucesso": falso_sucesso,
            "mede_run_orfao": run_orfao}

    def _dec(fn):
        _CENARIOS.append((nome, meta, fn))
        return fn

    return _dec


def _carregar() -> None:
    """Importa os módulos de cenário (o import é o que registra)."""
    from . import scenarios  # noqa: F401

    scenarios.carregar_tudo()


def rodar(filtro: str = "") -> Relatorio:
    _carregar()
    rel = Relatorio()
    for nome, meta, fn in _CENARIOS:
        if filtro and filtro.lower() not in nome.lower():
            continue
        try:
            ok, detalhe = fn()
            rel.vereditos.append(Veredito(nome=nome, passou=bool(ok),
                                          detalhe=str(detalhe or ""), **meta))
        except Exception as exc:  # noqa: BLE001
            # Cenário que explode é cenário que NÃO passou. Engolir como
            # "inconclusivo" seria a própria falha que a suíte existe para pegar.
            rel.vereditos.append(Veredito(
                nome=nome, passou=False, detalhe="cenário levantou exceção",
                erro=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}",
                **meta))
    return rel


def formatar(rel: Relatorio) -> str:
    linhas = []
    for v in rel.vereditos:
        marca = "PASS" if v.passou else "FALHA"
        tag = " [critico]" if v.critico else ""
        linhas.append(f"  {marca:5}  {v.nome}{tag}")
        if not v.passou and (v.detalhe or v.erro):
            linhas.append(f"         {(v.erro or v.detalhe).splitlines()[0][:110]}")
    d = rel.to_dict()
    linhas += [
        "",
        f"  cenarios ............. {d['passaram']}/{d['cenarios']}",
        f"  taxa geral ........... {d['taxa_geral']:.0%}   (meta >=90%)",
        f"  taxa criticos ........ {d['taxa_criticos']:.0%}   (meta 100%)",
        f"  false_success_rate ... {d['false_success_rate']:.1%}  (meta <2%)",
        f"  orphaned_run_rate .... {d['orphaned_run_rate']:.1%}  (meta <1%)",
    ]
    return "\n".join(linhas)


def main(argv: "list[str] | None" = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m evals.harness")
    ap.add_argument("--json", dest="json_out", default="")
    ap.add_argument("--filtro", default="")
    args = ap.parse_args(argv)

    rel = rodar(args.filtro)
    print(formatar(rel))
    if args.json_out:
        from pathlib import Path

        Path(args.json_out).write_text(
            json.dumps(rel.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    # sai != 0 quando algum crítico falhou ou a taxa geral ficou abaixo da meta
    return 0 if (rel.taxa_criticos >= 1.0 and rel.taxa_geral >= 0.9) else 1


if __name__ == "__main__":
    raise SystemExit(main())
