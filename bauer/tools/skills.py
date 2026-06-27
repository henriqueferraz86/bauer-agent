"""Skills tools: gerencia/visualiza/lista skills YAML do workspace."""

from __future__ import annotations

import json

from .base import ToolError


class SkillsToolsMixin:

    _SKILLS_FILE = ".bauer_skills.json"

    def _load_skills(self) -> dict:
        p = self.workspace / self._SKILLS_FILE
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_skills(self, skills: dict) -> None:
        p = self.workspace / self._SKILLS_FILE
        p.write_text(json.dumps(skills, ensure_ascii=False, indent=2), encoding="utf-8")

    def _skill_manage(self, args: dict) -> str:
        action = str(args.get("action", "")).strip().lower()
        name = str(args.get("name", "")).strip()
        if not action:
            raise ToolError("skill_manage: 'action' é obrigatório (create|update|delete).")
        if not name:
            raise ToolError("skill_manage: 'name' é obrigatório.")

        skills = self._load_skills()

        if action == "delete":
            if name not in skills:
                raise ToolError(f"skill_manage: skill '{name}' não encontrada.")
            del skills[name]
            self._save_skills(skills)
            return f"[skill_manage] Skill '{name}' removida."

        if action in ("create", "update"):
            description = str(args.get("description", "")).strip()
            content = str(args.get("content", "")).strip()
            if not description:
                raise ToolError("skill_manage: 'description' é obrigatório para create/update.")
            if not content:
                raise ToolError("skill_manage: 'content' é obrigatório para create/update.")
            if action == "create" and name in skills:
                raise ToolError(
                    f"skill_manage: skill '{name}' já existe. Use action='update' para editar."
                )
            tags = args.get("tags", [])
            if not isinstance(tags, list):
                tags = [str(tags)]
            import time as _time
            now = _time.time()
            existing = skills.get(name, {})
            skills[name] = {
                "name": name,
                "description": description,
                "content": content,
                "tags": tags,
                "created_at": existing.get("created_at", now),
                "updated_at": now,
            }
            self._save_skills(skills)
            verb = "criada" if action == "create" else "atualizada"
            return f"[skill_manage] Skill '{name}' {verb}. Tags: {tags or '—'}."

        raise ToolError(f"skill_manage: action '{action}' inválida. Use create|update|delete.")

    def _skill_view(self, args: dict) -> str:
        name = str(args.get("name", "")).strip()
        if not name:
            raise ToolError("skill_view: 'name' é obrigatório.")
        skills = self._load_skills()
        if name not in skills:
            available = ", ".join(sorted(skills.keys())) or "(nenhuma)"
            raise ToolError(f"skill_view: skill '{name}' não encontrada. Disponíveis: {available}")
        s = skills[name]
        import time as _time
        lines = [
            f"[skill] {s['name']}",
            f"Descrição: {s['description']}",
            f"Tags: {', '.join(s.get('tags', [])) or '—'}",
            f"Criada: {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(s.get('created_at', 0)))}",
            f"Atualizada: {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(s.get('updated_at', 0)))}",
            "",
            "─── Conteúdo ───",
            s["content"],
        ]
        return "\n".join(lines)

    def _skills_list(self, args: dict) -> str:
        skills = self._load_skills()
        if not skills:
            return "[skills_list] Nenhuma skill registrada."
        filt = str(args.get("filter", "")).strip().lower()
        results = []
        for s in skills.values():
            if filt:
                tag_match = any(filt in t.lower() for t in s.get("tags", []))
                name_match = filt in s["name"].lower()
                desc_match = filt in s.get("description", "").lower()
                if not (tag_match or name_match or desc_match):
                    continue
            results.append(s)
        if not results:
            return f"[skills_list] Nenhuma skill encontrada para filtro '{filt}'."
        lines = [f"[skills_list] {len(results)} skill(s):"]
        for s in sorted(results, key=lambda x: x["name"]):
            tags = ", ".join(s.get("tags", [])) or "—"
            lines.append(f"  • {s['name']} [{tags}] — {s.get('description', '')[:80]}")
        return "\n".join(lines)
