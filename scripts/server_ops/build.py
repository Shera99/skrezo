# Внутри — CSS, JS и HTML-шаблон страницы: строки не режутся ради лимита длины.
# ruff: noqa: E501
"""Сборка пульта сервера: ``content.py`` → ``docs/SERVER_OPS.html``.

Запуск из корня репозитория: ``make server-ops`` или
``uv run python scripts/server_ops/build.py``. Страница статичная: весь текст и
команды видны без скрипта, JS добавляет только поиск, подстановку домена и
копирование. ``--fragment PATH`` дополнительно пишет фрагмент без
``<html>/<head>/<body>`` — в таком виде страницу публикуют артефактом Claude
(обёртку он добавляет сам).
"""

import argparse
import html
import re
from collections.abc import Sequence
from pathlib import Path

from content import DANGER, SECTIONS, WARN, C, S

ROOT = Path(__file__).resolve().parents[2]
DOCS_OUT = ROOT / "docs" / "SERVER_OPS.html"
DEFAULT_DOMAIN = "api.skrezo.com"
GENERATED_NOTE = (
    "<!-- Сгенерировано scripts/server_ops/build.py из scripts/server_ops/content.py. "
    "Руками не править: make server-ops -->"
)


def inline(text: str) -> str:
    esc = html.escape(text, quote=False)
    return re.sub(r"`([^`]+)`", r'<code class="ic">\1</code>', esc)


def code_html(cmd: str) -> str:
    lines = []
    for line in html.escape(cmd, quote=False).split("\n"):
        lines.append(f'<span class="c">{line}</span>' if line.lstrip().startswith("#") else line)
    out = "\n".join(lines)
    return out.replace("{{DOMAIN}}", f'<span class="v" data-v="domain">{DEFAULT_DOMAIN}</span>')


def attr(text: str) -> str:
    return html.escape(re.sub(r"\s+", " ", text).strip().lower(), quote=True)


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


CHIP = {DANGER: "danger", WARN: "warn"}


def render_item(c: C, section: S) -> str:
    chips = "".join(
        f'<span class="chip {CHIP[k]}">{html.escape(label)}</span>' for k, label in c.flags
    )
    search = " ".join(
        [
            c.title,
            c.desc,
            c.expect,
            c.cmd.replace("{{DOMAIN}}", ""),
            c.kw,
            section.nav,
            " ".join(label for _, label in c.flags),
        ]
    )
    expect = (
        f'<p class="expect"><span>Ожидается</span><span>{inline(c.expect)}</span></p>'
        if c.expect
        else ""
    )
    return (
        f'<article class="cmd" data-search="{attr(search)}">'
        f'<div class="cmd-head"><h3>{inline(c.title)}</h3>{chips}</div>'
        f'<p class="desc">{inline(c.desc)}</p>'
        f'<div class="code"><pre><code>{code_html(c.cmd)}</code></pre>'
        f'<button class="copy" type="button" aria-label="Скопировать: {html.escape(c.title, quote=True)}">Копировать</button></div>'
        f"{expect}</article>"
    )


MAP_ROWS = [
    (
        "API, 2 процесса",
        "skrezo-api@8001<br>skrezo-api@8002",
        "127.0.0.1:8001<br>127.0.0.1:8002",
        ".env<br>skrezo-api@.service",
        "journalctl -u 'skrezo-api@*'",
    ),
    ("Сокет Socket.IO", "внутри API", "/socket.io/", "nginx, location /socket.io/", "журнал API"),
    (
        "Воркеры arq, 2",
        "skrezo-worker@1<br>skrezo-worker@2",
        "нет порта",
        ".env<br>skrezo-worker@.service",
        "journalctl -u 'skrezo-worker@*'",
    ),
    ("Очередь заданий", "redis-server", "Redis, ключи arq:*", "WORKER_MAX_JOBS", "журнал воркеров"),
    (
        "nginx и HTTPS",
        "nginx",
        ":80 и :443",
        "/etc/nginx/sites-available/skrezo",
        "/var/log/nginx/",
    ),
    (
        "PgBouncer",
        "pgbouncer",
        "127.0.0.1:6432",
        "/etc/pgbouncer/pgbouncer.ini",
        "/var/log/postgresql/pgbouncer.log",
    ),
    (
        "PostgreSQL 16",
        "postgresql@16-main",
        "127.0.0.1:5432",
        "/etc/postgresql/16/main/conf.d/skrezo.conf",
        "/var/log/postgresql/postgresql-16-main.log",
    ),
    (
        "Redis 7",
        "redis-server",
        "127.0.0.1:6379",
        "/etc/redis/skrezo.conf",
        "/var/log/redis/redis-server.log",
    ),
    (
        "Бэкап",
        "skrezo-backup.timer",
        "01:30 UTC",
        "skrezo-backup.service",
        "journalctl -u skrezo-backup",
    ),
    ("Файлы пациентов", "нет", "/opt/skrezo/app/storage", "STORAGE_ROOT", "нет"),
    (
        "Ключи пушей",
        "нет",
        "/opt/skrezo/app/secrets",
        "APNS_KEY<br>FCM_SERVICE_ACCOUNT_JSON",
        "нет",
    ),
]

SETTINGS_ROWS = [
    (
        "DATABASE_URL",
        ".env",
        "127.0.0.1:6432/skrezo",
        "все запросы приложения идут через PgBouncer",
    ),
    (
        "DATABASE_DIRECT_URL",
        ".env",
        "127.0.0.1:5432/skrezo",
        "шина realtime, диспетчер событий и миграции мимо пулера",
    ),
    ("REDIS_URL", ".env", "127.0.0.1:6379/0", "очередь, пульс, лимиты, кэш ассистента"),
    ("DB_POOL_SIZE / DB_MAX_OVERFLOW", ".env", "10 / 20", "соединений с пулером на каждый процесс"),
    ("WORKER_MAX_JOBS", ".env", "25", "одновременных заданий в одном воркере"),
    ("PUBLIC_API_URL", ".env", "https://домен/api/v1", "адреса колбэков FreedomPay"),
    (
        "CABINET_URL / CORS_ORIGINS",
        ".env",
        "https://адрес кабинета",
        "возврат с оплаты и доступ кабинета к API",
    ),
    ("ROOT_PATH", ".env", "пусто", "префикс за прокси; на своём домене не нужен"),
    ("STORAGE_ROOT", ".env", "storage", "файлы пациентов в /opt/skrezo/app/storage"),
    (
        "PUSH_ENABLED, APNS_KEY, FCM_SERVICE_ACCOUNT_JSON",
        ".env",
        "true, secrets/…",
        "отправка пушей iOS и Android",
    ),
    (
        "pool_mode / max_prepared_statements",
        "pgbouncer.ini",
        "transaction / 500",
        "работа asyncpg через пулер",
    ),
    (
        "default_pool_size / max_client_conn",
        "pgbouncer.ini",
        "40 / 500",
        "соединений пулера с базой / клиентов пулера",
    ),
    (
        "shared_buffers / max_connections",
        "postgres conf.d",
        "8GB / 200",
        "память базы и потолок соединений",
    ),
    (
        "maxmemory / maxmemory-policy",
        "redis skrezo.conf",
        "2gb / noeviction",
        "очередь не вытесняется при нехватке памяти",
    ),
    ("client_max_body_size", "nginx", "25m", "размер загружаемых файлов"),
    ("TZ", "юниты systemd", "UTC", "время cron-задач и меток в логах"),
]


def table(
    head: Sequence[str], rows: Sequence[tuple[str, ...]], mono_cols: tuple[int, ...] = ()
) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = []
    for row in rows:
        tds = []
        for i, cell in enumerate(row):
            content = html.escape(cell, quote=False).replace("&lt;br&gt;", "<br>")
            if i in mono_cols:
                parts = content.split("<br>")
                content = "<br>".join(f'<code class="ic">{p}</code>' for p in parts)
            tds.append(f"<td>{content}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


MAP_TABLE = table(
    ["Что", "Юнит systemd", "Адрес", "Настройки", "Логи"], MAP_ROWS, mono_cols=(1, 2, 3, 4)
)
SETTINGS_TABLE = table(
    ["Настройка", "Где", "Значение", "На что влияет"], SETTINGS_ROWS, mono_cols=(0, 2)
)

MAP_NOTES = """<div class="notes">
<p class="note callout"><b>RabbitMQ в проекте нет.</b> Очередь фоновых заданий — arq поверх Redis, её проверки в разделе <a href="#workers">«Воркеры и очередь»</a>. Ставить RabbitMQ не нужно.</p>
<p class="note"><b>Сокет и шина.</b> Socket.IO работает внутри процессов API. Процессы передают друг другу события через Postgres, канал <code class="ic">realtime</code>, напрямую на 5432 мимо PgBouncer: в режиме transaction пулер не держит LISTEN.</p>
<p class="note"><b>Как запускать команды.</b> Из-под вашего пользователя с sudo, без <code class="ic">sudo -i</code>. Короткие <code class="ic">rcli</code>, <code class="ic">pgsu</code>, <code class="ic">pgapp</code>, <code class="ic">pgb</code>, <code class="ic">skrun</code> и <code class="ic">skr</code> заводятся один раз в разделе <a href="#prep">«Подготовка»</a>.</p>
</div>"""

STATIC = {"SETTINGS_TABLE": SETTINGS_TABLE}


def render_section(s: S) -> str:
    meta = "".join(f'<span class="tag">{html.escape(m)}</span>' for m in s.meta)
    meta_html = f'<div class="meta">{meta}</div>' if meta else ""
    static = STATIC.get(s.static, "")
    items = "".join(render_item(c, s) for c in s.items)
    search = " ".join([s.nav, s.title, s.lead, s.kw, strip_tags(static)])
    return (
        f'<section class="panel" id="{s.id}" data-search="{attr(search)}">'
        f'<header class="panel-head"><h2>{inline(s.title)}</h2><p class="lead">{inline(s.lead)}</p>{meta_html}</header>'
        f'{static}<div class="cmds">{items}</div></section>'
    )


def commands_word(n: int) -> str:
    """«1 команда», «3 команды», «171 команда», «12 команд»."""
    a, b = n % 10, n % 100
    if a == 1 and b != 11:
        return "команда"
    if 2 <= a <= 4 and not 12 <= b <= 14:
        return "команды"
    return "команд"


def build() -> str:
    total = sum(len(s.items) for s in SECTIONS)
    toc = ['<a href="#map"><span>Карта сервисов</span></a>']
    for s in SECTIONS:
        toc.append(
            f'<a href="#{s.id}"><span>{html.escape(s.nav)}</span><span class="n">{len(s.items)}</span></a>'
        )
    map_section = (
        '<section class="panel" id="map" data-search="'
        + attr("карта сервисов rabbitmq rabbit очередь брокер " + strip_tags(MAP_TABLE + MAP_NOTES))
        + '"><header class="panel-head"><h2>Карта сервисов</h2>'
        '<p class="lead">Что запущено на сервере, где это настраивается и куда пишет логи.</p></header>'
        + MAP_TABLE
        + MAP_NOTES
        + "</section>"
    )
    sections = map_section + "".join(render_section(s) for s in SECTIONS)
    page = TEMPLATE
    page = page.replace("%%CSS%%", CSS).replace("%%JS%%", JS)
    page = page.replace("%%TOC%%", "".join(toc)).replace("%%SECTIONS%%", sections)
    page = page.replace("%%TOTAL%% команд", f"{total} {commands_word(total)}").replace(
        "%%DOMAIN%%", DEFAULT_DOMAIN
    )
    return page


CSS = r"""
/* Пульт дежурного: слева шкала разделов по слоям системы, справа панели сервисов с командами */
:root{
  --bg:#ECEFF3;--panel:#FFFFFF;--ink:#152230;--ink-2:#4D5B69;--rule:#D5DCE4;--ic-bg:#E5EAF0;
  --accent:#2949D2;--accent-soft:#E2E8FC;
  --ok:#17784A;--warn:#955800;--warn-soft:#FBEFD9;--danger:#B42519;--danger-soft:#FBE8E5;
  --code-bg:#101923;--code-ink:#E2E9F0;--code-dim:#7C8B99;--code-rule:#2B3846;--code-var:#A8BBFF;--code-ok:#72D6A3;
  --f-display:"Unbounded","Golos Text",system-ui,sans-serif;
  --f-body:"Golos Text",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --f-mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --radius:10px;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0C1218;--panel:#131B23;--ink:#E2E8EE;--ink-2:#93A1AF;--rule:#25303B;--ic-bg:#1C2631;
  --accent:#8EA3FF;--accent-soft:#1A2448;
  --ok:#5BCB94;--warn:#E6A846;--warn-soft:#33260F;--danger:#FF8B80;--danger-soft:#3B1815;
  --code-bg:#0A0F15;--code-ink:#E2E9F0;--code-dim:#7A8896;--code-rule:#26313D;--code-var:#A8BBFF;--code-ok:#72D6A3;
  color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0C1218;--panel:#131B23;--ink:#E2E8EE;--ink-2:#93A1AF;--rule:#25303B;--ic-bg:#1C2631;
  --accent:#8EA3FF;--accent-soft:#1A2448;
  --ok:#5BCB94;--warn:#E6A846;--warn-soft:#33260F;--danger:#FF8B80;--danger-soft:#3B1815;
  --code-bg:#0A0F15;--code-ink:#E2E9F0;--code-dim:#7A8896;--code-rule:#26313D;--code-var:#A8BBFF;--code-ok:#72D6A3;
  color-scheme:dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--f-body);font-size:15px;line-height:1.55;-webkit-text-size-adjust:100%}
a{color:var(--accent)}
a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{max-width:1280px;margin-inline:auto;padding-inline:clamp(16px,3vw,36px);padding-block:28px 72px}
.top{display:grid;gap:12px;padding-block:8px 20px;max-width:880px}
.eyebrow{margin:0;font-family:var(--f-mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2)}
h1{margin:0;font-family:var(--f-display);font-weight:600;font-size:clamp(28px,4.4vw,44px);line-height:1.08;letter-spacing:-.02em;text-wrap:balance}
.lede{margin:0;font-size:16.5px;color:var(--ink-2);max-width:66ch}
.legend{list-style:none;margin:4px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:8px 20px;font-size:13.5px;color:var(--ink-2)}
.legend li{display:flex;align-items:center;gap:8px}
.toolbar{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px 18px;padding-block:12px;border-block:1px solid var(--rule);background:var(--bg)}
@media (min-width:760px){.toolbar{position:sticky;top:env(safe-area-inset-top,0px);z-index:10}}
.field{display:grid;gap:4px;min-width:0}
.field.grow{flex:1 1 300px}
.field label{font-family:var(--f-mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2)}
.field input{font:inherit;font-size:15px;color:var(--ink);background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:9px 12px;width:100%;min-width:0}
.field input::placeholder{color:var(--ink-2);opacity:.75}
.field input:focus-visible{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
#domain{font-family:var(--f-mono);font-size:14px;width:min(100%,260px)}
.domain-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hint{font-size:12.5px;color:var(--ink-2)}
.count{margin:0 0 10px auto;font-family:var(--f-mono);font-size:12.5px;color:var(--ink-2);font-variant-numeric:tabular-nums}
.layout{display:grid;gap:22px;margin-top:22px}
@media (min-width:1020px){.layout{grid-template-columns:204px minmax(0,1fr);align-items:start}}
.toc{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px;min-width:0}
.toc a{flex:none;display:flex;justify-content:space-between;align-items:baseline;gap:10px;text-decoration:none;color:var(--ink-2);font-size:14px;padding:6px 10px;border-radius:7px;border:1px solid var(--rule);background:var(--panel)}
.toc a:hover{color:var(--ink)}
.toc a.on{color:var(--accent);border-color:var(--accent)}
.toc a.off{opacity:.38}
.toc .n{font-family:var(--f-mono);font-size:11.5px;font-variant-numeric:tabular-nums}
@media (min-width:1020px){
  .toc{position:sticky;top:calc(env(safe-area-inset-top,0px) + 96px);flex-direction:column;gap:1px;max-height:calc(100vh - 116px);overflow:auto;padding-bottom:0}
  .toc a{border:0;border-left:2px solid var(--rule);border-radius:0;background:transparent;padding:5px 10px 5px 12px}
  .toc a.on{border-left-color:var(--accent);background:var(--accent-soft)}
}
main{display:grid;gap:22px;min-width:0}
.panel{background:var(--panel);border:1px solid var(--rule);border-radius:var(--radius);scroll-margin-top:16px;min-width:0}
@media (min-width:760px){.panel{scroll-margin-top:100px}}
.panel-head{display:grid;gap:8px;padding:20px 22px 16px;border-bottom:1px solid var(--rule)}
.panel-head h2{margin:0;font-family:var(--f-display);font-weight:600;font-size:19px;line-height:1.25;letter-spacing:-.01em;text-wrap:balance}
.panel-head .lead{margin:0;color:var(--ink-2);max-width:80ch}
.meta{display:flex;flex-wrap:wrap;gap:6px}
.tag{font-family:var(--f-mono);font-size:11.5px;line-height:1.5;padding:2px 8px;border-radius:5px;background:var(--ic-bg);color:var(--ink-2);overflow-wrap:anywhere}
.cmd{display:grid;gap:8px;padding:16px 22px;min-width:0}
.cmd + .cmd{border-top:1px solid var(--rule)}
.cmd-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
.cmd h3{margin:0;font-size:15.5px;font-weight:600;line-height:1.35}
.desc{margin:0;color:var(--ink-2);max-width:80ch}
.expect{margin:0;font-size:13.5px;color:var(--ink-2);display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px}
.expect>span:first-child{font-family:var(--f-mono);font-size:10.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--ok)}
.chip{font-family:var(--f-mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.chip.warn{color:var(--warn);background:var(--warn-soft)}
.chip.danger{color:var(--danger);background:var(--danger-soft)}
.code{display:flex;align-items:flex-start;gap:10px;background:var(--code-bg);border-radius:8px;padding:10px 10px 10px 14px;min-width:0}
.code pre{flex:1;min-width:0;margin:0;padding-block:3px;font-family:var(--f-mono);font-size:13.5px;line-height:1.6;color:var(--code-ink);white-space:pre-wrap;overflow-wrap:anywhere;tab-size:4}
.code .c{color:var(--code-dim)}
.code .v{color:var(--code-var);text-decoration:underline dotted;text-underline-offset:3px}
.copy{flex:none;font-family:var(--f-mono);font-size:12px;color:var(--code-ink);background:transparent;border:1px solid var(--code-rule);border-radius:6px;padding:5px 10px;cursor:pointer;transition:border-color .15s,color .15s}
.copy:hover{border-color:var(--code-dim)}
.copy:focus-visible{outline:2px solid var(--code-var);outline-offset:2px}
.copy.done{color:var(--code-ok);border-color:var(--code-ok)}
code.ic{font-family:var(--f-mono);font-size:.88em;background:var(--ic-bg);color:var(--ink);padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}
.table-wrap{overflow-x:auto;padding:4px 22px 8px}
table{width:100%;min-width:760px;border-collapse:collapse;font-size:14px}
th{font-family:var(--f-mono);font-size:10.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2);text-align:left;padding:12px 10px 8px;border-bottom:1px solid var(--rule)}
td{padding:9px 10px;border-bottom:1px solid var(--rule);vertical-align:top;line-height:1.7}
tr:last-child td{border-bottom:0}
td:first-child{font-weight:600}
.notes{display:grid;gap:10px;padding:14px 22px 20px;border-top:1px solid var(--rule)}
.note{margin:0;color:var(--ink-2);max-width:88ch}
.note b{color:var(--ink)}
.callout{padding:10px 14px;border-radius:8px;background:var(--accent-soft);color:var(--ink)}
.empty{margin:0;padding:28px 22px;text-align:center;color:var(--ink-2);background:var(--panel);border:1px dashed var(--rule);border-radius:var(--radius)}
.foot{margin:6px 0 0;font-size:13px;color:var(--ink-2)}
@media (max-width:520px){.cmd,.panel-head,.notes{padding-inline:16px}.table-wrap{padding-inline:16px}.code{padding-left:12px}.code pre{font-size:13px}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important}}
"""

JS = r"""
(function () {
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  // Домен API подставляется во все команды и запоминается в этом браузере.
  var KEY = 'skrezo-ops-domain';
  var domain = $('#domain');
  function setDomain() {
    var value = domain.value.trim() || domain.placeholder;
    $$('.v[data-v="domain"]').forEach(function (el) { el.textContent = value; });
  }
  try { var saved = localStorage.getItem(KEY); if (saved) domain.value = saved; } catch (e) {}
  setDomain();
  domain.addEventListener('input', function () {
    setDomain();
    try { localStorage.setItem(KEY, domain.value.trim()); } catch (e) {}
  });

  // Поиск по названию, описанию и тексту команды.
  var q = $('#q');
  var items = $$('.cmd');
  var sections = $$('section.panel');
  var countEl = $('#count');
  var empty = $('#empty');
  function plural(n, one, few, many) {
    var a = n % 10, b = n % 100;
    if (a === 1 && b !== 11) return one;
    if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
    return many;
  }
  function apply() {
    var tokens = q.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    var shown = 0;
    items.forEach(function (it) {
      var ok = tokens.every(function (t) { return it.dataset.search.indexOf(t) !== -1; });
      it.hidden = !ok;
      if (ok) shown++;
    });
    var anySection = false;
    sections.forEach(function (sec) {
      var own = $$('.cmd', sec);
      var visible = own.filter(function (i) { return !i.hidden; }).length;
      var selfMatch = tokens.every(function (t) { return (sec.dataset.search || '').indexOf(t) !== -1; });
      var show = tokens.length === 0 || visible > 0 || (own.length === 0 && selfMatch);
      sec.hidden = !show;
      if (show) anySection = true;
      var link = $('.toc a[href="#' + sec.id + '"]');
      if (link) {
        link.classList.toggle('off', !show);
        var n = $('.n', link);
        if (n) n.textContent = tokens.length ? visible : own.length;
      }
    });
    countEl.textContent = tokens.length
      ? 'Найдено ' + shown + ' из ' + items.length
      : items.length + ' ' + plural(items.length, 'команда', 'команды', 'команд');
    empty.hidden = anySection;
  }
  q.addEventListener('input', apply);
  apply();

  document.addEventListener('keydown', function (e) {
    var tag = document.activeElement && document.activeElement.tagName;
    if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') { e.preventDefault(); q.focus(); }
    else if (e.key === 'Escape' && document.activeElement === q && q.value) { q.value = ''; apply(); }
  });

  // Копирование: текст берётся с уже подставленным доменом.
  function flash(btn, label) {
    btn.classList.add('done');
    btn.textContent = label;
    clearTimeout(btn._t);
    btn._t = setTimeout(function () { btn.classList.remove('done'); btn.textContent = 'Копировать'; }, 1600);
  }
  function selectCode(code) {
    var range = document.createRange();
    range.selectNodeContents(code);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.copy');
    if (!btn) return;
    var code = btn.parentNode.querySelector('code');
    var text = code.textContent;
    try {
      navigator.clipboard.writeText(text).then(
        function () { flash(btn, 'Скопировано'); },
        function () { selectCode(code); flash(btn, 'Выделено'); }
      );
    } catch (err) { selectCode(code); flash(btn, 'Выделено'); }
  });

  // Подсветка текущего раздела в оглавлении.
  if ('IntersectionObserver' in window) {
    var links = {};
    $$('.toc a').forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        Object.keys(links).forEach(function (k) { links[k].classList.remove('on'); });
        if (links[en.target.id]) links[en.target.id].classList.add('on');
      });
    }, { rootMargin: '-25% 0px -65% 0px' });
    sections.forEach(function (s) { io.observe(s); });
  }
})();
"""

TEMPLATE = """<title>Пульт сервера Skrezo</title>
<meta name="description" content="Команды, настройки и проверки прод-сервера Skrezo.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600&family=JetBrains+Mono:wght@400;600&family=Unbounded:wght@500;600&display=swap">
<style>%%CSS%%</style>
<div class="wrap">
  <header class="top">
    <p class="eyebrow">Ubuntu 24.04 · 12 CPU · 47 ГБ · systemd без Docker</p>
    <h1>Пульт сервера Skrezo</h1>
    <p class="lede">Команды, настройки и проверки прод-сервера: базы, Redis, очереди, сокета, воркеров, nginx и бэкапов. У каждой команды написано, что она делает и что должно получиться.</p>
    <ul class="legend">
      <li><span class="chip warn">простой</span>сайт ненадолго недоступен или нужен перезапуск</li>
      <li><span class="chip danger">меняет данные</span>удаляет или перезаписывает, вернуть нельзя</li>
    </ul>
  </header>
  <div class="toolbar">
    <div class="field grow">
      <label for="q">Поиск по командам</label>
      <input id="q" type="search" placeholder="очередь, 502, бэкап, пуши…  ( / — сюда )" autocomplete="off" spellcheck="false">
    </div>
    <div class="field">
      <label for="domain">Домен API</label>
      <div class="domain-row">
        <input id="domain" type="text" placeholder="%%DOMAIN%%" autocomplete="off" spellcheck="false" aria-describedby="domain-hint">
        <span class="hint" id="domain-hint">подставится во все команды</span>
      </div>
    </div>
    <p class="count" id="count" aria-live="polite">%%TOTAL%% команд</p>
  </div>
  <div class="layout">
    <nav class="toc" aria-label="Разделы">%%TOC%%</nav>
    <main>
      %%SECTIONS%%
      <p class="empty" id="empty" hidden>Ничего не нашлось. Попробуйте другое слово: «очередь», «502», «бэкап», «пуши».</p>
      <p class="foot">Собрано по репозиторию Skrezo: каталог deploy/ и инструкция docs/DEPLOY.md. Октябрь 2026.</p>
    </main>
  </div>
</div>
<script>%%JS%%</script>
"""


def standalone(fragment: str) -> str:
    """Полный документ для docs/: doctype, кодировка, viewport; тело — тот же фрагмент."""
    head, body = fragment.split('<div class="wrap">', 1)
    return (
        "<!doctype html>\n"
        + GENERATED_NOTE
        + '\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        + head.strip()
        + '\n</head>\n<body>\n<div class="wrap">'
        + body.rstrip()
        + "\n</body>\n</html>\n"
    )


def main() -> None:
    """Собрать страницу в docs/ и, по запросу, фрагмент для артефакта."""
    parser = argparse.ArgumentParser(description="Пересобрать пульт сервера Skrezo")
    parser.add_argument("--fragment", type=Path, help="записать ещё и фрагмент для артефакта")
    args = parser.parse_args()
    page = build()
    DOCS_OUT.write_text(standalone(page), encoding="utf-8")
    commands = sum(len(s.items) for s in SECTIONS)
    print(
        f"{DOCS_OUT.relative_to(ROOT)}: {commands} {commands_word(commands)}, {DOCS_OUT.stat().st_size} байт"
    )
    if args.fragment:
        args.fragment.write_text(page, encoding="utf-8")
        print(f"фрагмент: {args.fragment}")


if __name__ == "__main__":
    main()
