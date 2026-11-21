"""Fase 16 — redesign do painel e auditoria: contratos estáticos da interface e cabeçalhos de cache."""

from pathlib import Path
import re

from fastapi.testclient import TestClient

from rocha_reis_agent import RELEASE_NAME, __version__
from rocha_reis_agent.panel.app import create_panel_app
from panel_support import ORIGIN, panel_settings

STATIC = Path(__file__).resolve().parents[1] / "rocha_reis_agent" / "panel" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")


def test_static_panel_files_are_always_revalidated_and_api_is_never_stored(tmp_path):
    # Sem isso o navegador podia reaproveitar um app.js antigo com um index.html novo depois de uma
    # atualização (ou o contrário) e o painel quebrava até limpar o cache.
    client = TestClient(create_panel_app(panel_settings(tmp_path)), base_url=ORIGIN)
    for path in ("/panel/", "/panel/app.js", "/panel/styles.css"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path
        assert response.headers.get("etag") or response.headers.get("last-modified"), path  # revalidação barata
    assert client.get("/api/panel/auth/session").headers["cache-control"] == "no-store"


def test_every_element_id_used_by_the_script_exists_once_in_the_page():
    used = set(re.findall(r'\$\("([a-z0-9-]+)"\)', JS))
    created = set(re.findall(r'id: "([a-z0-9-]+)"', JS))  # campos criados dentro de diálogos
    declared = re.findall(r'\bid="([^"]+)"', HTML)
    missing = sorted(used - set(declared) - created)
    assert not missing, missing
    duplicated = sorted({i for i in declared if declared.count(i) > 1})
    assert not duplicated, duplicated


def test_design_system_has_no_external_resources_and_keeps_both_themes():
    # CSP: style-src/img-src 'self' (+ data:). Nenhuma fonte, imagem ou folha externa.
    for url in re.findall(r"url\(([^)]*)\)", CSS):
        assert url.strip("\"' ").startswith("data:image/svg+xml"), url[:60]
    assert "@import" not in CSS and "http://" not in CSS.replace("http://www.w3.org/2000/svg", "")
    assert "@media (prefers-color-scheme: dark)" in CSS
    root_light = CSS[CSS.index(":root {"):CSS.index("}", CSS.index(":root {"))]
    dark_start = CSS.index("@media (prefers-color-scheme: dark)")
    root_dark = CSS[dark_start:CSS.index("}", CSS.index(":root {", dark_start))]
    for token in ("--bg", "--surface", "--ink", "--muted", "--line", "--accent", "--accent-ink", "--success", "--warning",
                  "--danger", "--danger-solid", "--focus"):
        assert f"{token}:" in root_light and f"{token}:" in root_dark, token
    # Regressões já corrigidas que o redesign não pode desfazer (Fases 10 e 12).
    assert ".table-wrap { position: relative;" in CSS
    assert ".toasts { pointer-events: none; }" in CSS


def test_script_still_never_injects_html():
    code = "\n".join(line for line in JS.splitlines() if not line.lstrip().startswith("//"))
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code and "outerHTML =" not in code
    assert "<script>" not in HTML and " style=" not in HTML and "onclick" not in HTML.lower()


def test_release_identification_moved_with_the_redesign():
    assert RELEASE_NAME == "Rocha Reis Agent — RC1 Offline"
    assert __version__ == "0.14.0rc1.post2"
