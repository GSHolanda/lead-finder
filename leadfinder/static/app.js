"use strict";

const S = { meta: null, tab: "campanha", leads: [], selected: null, detail: null,
  filters: { q: "", segment: "", status: "" }, pilotId: null, health: null };

const $ = (sel, root = document) => root.querySelector(sel);
const main = () => $("#main");

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? esc(iso) : d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}
function link(url, label) {
  if (!url) return "—";
  const safe = /^https?:\/\//i.test(url) ? url : "https://" + url;
  return `<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(label || url)}</a>`;
}
function badge(status, label) { return `<span class="badge s-${esc(status)}">${esc(label || status)}</span>`; }
function toast(msg, ms = 3500) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}
async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.erro || `HTTP ${res.status}`);
  return data;
}
async function guarded(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { toast("Erro: " + e.message, 6000); }
}

const ASSOC = { fonte: "veio da fonte", agregador: "link na bio da empresa", usuario: "informado por você",
  evidencia: "confirmado pelo conteúdo", nao_confirmado: "NÃO confirmado (só nome)" };
const CHECK = { link_registro: "Link do registro", acesso_pagina: "Acesso à página", agregador: "Agregador de links",
  perfil_publico: "Perfil público", busca: "Busca web", dominio_provavel: "Domínio provável" };
const RESULT = { confirmado: "confirmado", existe: "existe (associação pela fonte)", bloqueado: "bloqueado (não lido)",
  indisponivel: "indisponível", estacionado: "estacionado/padrão", redireciona: "redireciona", diretorio: "diretório",
  so_nome: "só o nome confere", homonimo: "provável homônimo", sem_relacao: "sem relação", nao_corresponde: "não corresponde",
  nao_conferido: "não conferido", confirmado_trecho: "confirmado pelo trecho da busca", erro: "erro" };

// ---------------------------------------------------------------- saúde / abas
async function loadHealth() {
  try {
    const h = await api("GET", "/api/health"); S.health = h;
    const o = h.ollama, b = h.busca;
    $("#health").innerHTML = [
      `<span class="pill ${o.disponivel ? "ok" : "bad"}" title="${esc(o.url)}">Ollama: ${o.disponivel ? esc(o.modelo) : "indisponível"}</span>`,
      `<span class="pill ${b.ativo ? "ok" : "warn"}">Busca: ${b.ativo ? esc(b.provedor) : "não configurada"}</span>`,
      h.vendedor_configurado ? "" : `<span class="pill warn">Preencha seu nome em Configurações</span>`,
    ].join("");
    setCounts(h.contagens);
  } catch (e) { $("#health").innerHTML = `<span class="pill bad">servidor indisponível</span>`; }
}
function setCounts(c) {
  if (!c) return;
  document.querySelectorAll("[data-count]").forEach(el => { el.textContent = c[el.dataset.count] ?? 0; });
}
document.getElementById("tabs").addEventListener("click", e => {
  const b = e.target.closest("button[data-tab]"); if (!b) return;
  document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("active", x === b));
  S.tab = b.dataset.tab; S.selected = null; S.detail = null; render();
});

function render() {
  main().onclick = null;
  if (["campanha", "verificar", "com_site", "todos"].includes(S.tab)) return renderLeads();
  if (S.tab === "descoberta") return renderDiscovery();
  if (S.tab === "piloto") return renderPilot();
  if (S.tab === "config") return renderConfig();
}

// ---------------------------------------------------------------- lista de leads
const VIEW_HELP = {
  campanha: "Site próprio não localizado após verificação suficiente, com prioridade pela qualificação. Nada é enviado automaticamente.",
  verificar: "Pendentes, inconclusivos, possíveis sites fora do ar e casos fora da campanha (ex.: redes/franquias). Confirme manualmente antes de prospectar.",
  com_site: "Empresas com site próprio encontrado — fora da campanha principal.",
  todos: "Todos os leads.",
};
async function renderLeads() {
  const segOpts = Object.entries(S.meta.segmentos).map(([k, v]) => `<option value="${k}" ${S.filters.segment === k ? "selected" : ""}>${esc(v)}</option>`).join("");
  const stOpts = Object.entries(S.meta.site_status).map(([k, v]) => `<option value="${k}" ${S.filters.status === k ? "selected" : ""}>${esc(v)}</option>`).join("");
  main().innerHTML = `
    <div class="alert info">${esc(VIEW_HELP[S.tab])}</div>
    <div class="split">
      <div>
        <div class="card" style="padding:10px 12px">
          <div class="toolbar">
            <input id="f-q" class="grow" placeholder="Buscar nome, bairro, cidade" value="${esc(S.filters.q)}">
            <select id="f-seg"><option value="">Todos os segmentos</option>${segOpts}</select>
            ${S.tab === "todos" ? `<select id="f-st"><option value="">Todas as classificações</option>${stOpts}</select>` : ""}
          </div>
          <div class="toolbar small">
            ${S.tab === "verificar" ? `<button class="btn" id="batch">Verificar lote (até 25)</button>` : ""}
            <a class="btn" href="/api/export.csv?view=${S.tab}" style="text-decoration:none">Exportar CSV</a>
            <span class="muted" id="n-leads"></span>
          </div>
        </div>
        <div class="card list" id="lead-list"><div class="empty">Carregando…</div></div>
      </div>
      <div id="detail"><div class="card empty">Selecione um lead para ver a verificação de site, o contato, o resumo e a mensagem.</div></div>
    </div>`;
  let t;
  $("#f-q").addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => { S.filters.q = e.target.value; loadLeads(); }, 250); });
  $("#f-seg").addEventListener("change", e => { S.filters.segment = e.target.value; loadLeads(); });
  if ($("#f-st")) $("#f-st").addEventListener("change", e => { S.filters.status = e.target.value; loadLeads(); });
  if ($("#batch")) $("#batch").addEventListener("click", () => startJob("/api/jobs/process", { view: "verificar", limit: 25 }));
  await loadLeads();
  if (S.selected) loadDetail(S.selected);
}
async function loadLeads() {
  const p = new URLSearchParams({ view: S.tab, q: S.filters.q, segment: S.filters.segment, status: S.tab === "todos" ? S.filters.status : "" });
  const data = await guarded(() => api("GET", "/api/leads?" + p));
  if (!data) return;
  S.leads = data.leads; setCounts(data.contagens);
  $("#n-leads").textContent = `${S.leads.length} lead(s)`;
  const list = $("#lead-list");
  if (!S.leads.length) { list.innerHTML = `<div class="empty">Nenhum lead nesta lista.</div>`; return; }
  list.innerHTML = S.leads.map(l => `
    <div class="item ${S.selected === l.id ? "sel" : ""}" data-id="${l.id}">
      <div class="name"><span>${esc(l.nome)}</span>${l.score != null && S.tab === "campanha" ? `<span class="score">${Math.round(l.score)}</span>` : ""}</div>
      <div class="meta">${esc(l.segmento)}${l.local ? " · " + esc(l.local) : ""}</div>
      <div class="meta">${badge(l.status, l.status_label)} ${l.manual ? "· correção humana" : `· confiança ${esc(l.confianca_label)}`}${l.rede ? " · rede/franquia?" : ""}${l.tem_contato ? "" : " · sem contato"}</div>
      ${l.proxima_acao ? `<div class="action">→ ${esc(l.proxima_acao)}</div>` : ""}
    </div>`).join("");
  list.onclick = e => { const it = e.target.closest(".item"); if (it) loadDetail(+it.dataset.id); };
}
async function loadDetail(id) {
  S.selected = id;
  document.querySelectorAll(".item").forEach(x => x.classList.toggle("sel", +x.dataset.id === id));
  const d = await guarded(() => api("GET", `/api/leads/${id}`));
  if (d) { S.detail = d; renderDetail(); }
}
async function act(promise, okMsg) {
  const r = await guarded(() => promise, okMsg);
  if (r && r.lead && r.site) { S.detail = r; renderDetail(); }
  else if (r && r.lead && r.lead.lead) { S.detail = r.lead; renderDetail(); }
  loadLeads(); loadHealth();
  return r;
}

// ---------------------------------------------------------------- detalhe
function renderDetail() {
  const d = S.detail, L = d.lead, site = d.site, q = d.qualificacao;
  const srcs = d.fontes.map(s => s.source_url ? link(s.source_url, s.source.toUpperCase()) : esc(s.source)).join(", ");
  const statusOpts = S.meta.site_status_editaveis.map(k => `<option value="${k}">${esc(S.meta.site_status[k])}</option>`).join("");
  const origOpts = Object.entries(S.meta.origens_manuais).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  const funilOpts = Object.entries(S.meta.funil).map(([k, v]) => `<option value="${k}" ${L.pipeline_status === k ? "selected" : ""}>${esc(v)}</option>`).join("");
  const run = site.ultima_execucao;
  const assocLinks = d.links.filter(l => l.association !== "nao_confirmado");
  const unconfirmed = d.links.filter(l => l.association === "nao_confirmado");
  const msg = d.mensagem;
  const apps = q.aplicacoes || { com_evidencia: [], sugestoes_segmento: [] };
  const comp = q.composicao || {};
  const c = d.contato;

  $("#detail").innerHTML = `
  <div class="card">
    <h2>${esc(L.name)} ${badge(site.status, site.status_label)}</h2>
    <div class="muted small">${esc(d.segmento_label)}${L.category_raw ? " · " + esc(L.category_raw) : ""} · ${esc([L.street, L.housenumber].filter(Boolean).join(" "))} ${esc([L.neighborhood, L.city, L.uf].filter(Boolean).join(", "))}</div>
    <div class="small" style="margin-top:6px">Fontes: ${srcs || "—"} · Prioridade de descoberta: <b>${esc(L.discovery_priority)}</b> <span class="muted">(${esc(L.discovery_note || "")})</span></div>
    ${L.is_chain ? `<div class="alert warn">Possível rede/franquia: ${esc(L.chain_reason || "")} <button class="btn small" id="unchain">Não é rede — incluir</button></div>` : ""}
    <div class="alert info next">Próxima ação sugerida: ${esc(q.proxima_acao)}</div>
  </div>

  <div class="card">
    <h2>Existência de site próprio</h2>
    <div class="kv">
      <div>Classificação vigente</div><div>${badge(site.status, site.status_label)} ${site.manual.status ? "<b>correção humana</b>" : "automática"}</div>
      <div>Confiança</div><div>${site.manual.status ? "confirmada manualmente (" + esc(site.manual.origem) + ")" : esc(site.auto.confianca_label) + (site.auto.confianca != null ? ` (${site.auto.confianca.toFixed(2)})` : "")}</div>
      <div>Site</div><div>${site.url ? link(site.url) : "—"}</div>
      <div>Motivo (automático)</div><div class="small">${esc(site.auto.motivo || "ainda não verificado")}</div>
      <div>Verificado em</div><div>${fmtDate(site.auto.verificado_em)}</div>
    </div>
    ${site.manual.status ? `<div class="alert info">Correção humana: <b>${esc(site.manual.label)}</b> — ${esc(site.manual.origem || "")}${site.manual.nota ? " — " + esc(site.manual.nota) : ""} (${fmtDate(site.manual.em)}). A verificação automática indica <b>${esc(site.auto.label || "—")}</b> e não sobrescreve sua correção. <button class="btn small" id="clear-manual">Remover correção</button></div>` : ""}
    <div class="toolbar" style="margin-top:10px"><button class="btn primary" id="verify">Verificar agora</button>
      <span class="muted small">Falha de consulta ≠ ausência de site. Domínio fora do ar ≠ ausência de site.</span></div>

    <h3>Conferir manualmente</h3>
    <div class="links small">${site.busca_manual.map(x => link(x.url, x.label)).join("")}</div>

    <h3>Consultas da última verificação ${run ? `(#${run.id}, ${fmtDate(run.started_at)}, busca: ${esc(run.summary.provedor_busca)})` : ""}</h3>
    ${site.consultas.length ? `<table><tr><th>Etapa</th><th>Fonte</th><th>Consulta/URL</th><th>Data</th><th>Resultado</th></tr>
      ${site.consultas.map(k => `<tr><td>${esc(CHECK[k.check_type] || k.check_type)}</td><td>${esc(k.source || "")}</td>
        <td class="wrap">${k.query ? esc(k.query) : ""}${k.url ? link(k.url) : ""}${k.detail ? `<div class="muted">${esc(k.detail)}</div>` : ""}</td>
        <td>${fmtDate(k.performed_at)}</td><td class="o-${esc(k.outcome)}">${esc(k.outcome)}</td></tr>`).join("")}</table>`
      : `<div class="muted small">Nenhuma verificação executada.</div>`}
    ${run && run.summary.avaliacoes && run.summary.avaliacoes.length ? `<h3>Candidatos a site avaliados</h3>
      <table><tr><th>URL</th><th>Origem</th><th>Resultado</th></tr>${run.summary.avaliacoes.map(a => `<tr><td class="wrap">${link(a.url)}</td><td>${esc(a.origem)}<div class="muted">${esc(a.associacao)}</div></td><td><b>${esc(RESULT[a.resultado] || a.resultado)}</b><div class="muted">${esc(a.detalhe)}</div></td></tr>`).join("")}</table>` : ""}

    <h3>Links associados à empresa</h3>
    ${assocLinks.length ? `<table><tr><th>Tipo</th><th>Link</th><th>Como sabemos</th></tr>${assocLinks.map(l => `<tr><td>${esc(l.tipo_label)}${l.platform ? `<div class="muted">${esc(l.platform)}</div>` : ""}</td><td class="wrap">${link(l.url)}</td><td>${esc(ASSOC[l.association] || l.association)}<div class="muted">${esc(l.origin)}${l.last_result ? " · " + esc(RESULT[l.last_result] || l.last_result) : ""}</div></td></tr>`).join("")}</table>` : `<div class="muted small">Nenhum link nas fontes.</div>`}
    ${unconfirmed.length ? `<details><summary>${unconfirmed.length} link(s) não confirmados (só semelhança de nome — não associados)</summary><div class="small">${unconfirmed.map(l => link(l.url) + " <span class='muted'>(" + esc(l.tipo_label) + ")</span>").join("<br>")}</div></details>` : ""}

    <h3>Corrigir classificação</h3>
    <div class="form-row">
      <select id="m-status">${statusOpts}</select>
      <input id="m-url" class="grow" placeholder="URL do site (obrigatória se encontrado)">
    </div>
    <div class="form-row">
      <select id="m-src">${origOpts}</select>
      <input id="m-note" class="grow" placeholder="Nota (ex.: confirmado por telefone em 19/11)">
      <button class="btn" id="m-save">Salvar correção</button>
    </div>
    ${site.execucoes.length > 1 ? `<details><summary>Verificações anteriores (${site.execucoes.length})</summary><table>${site.execucoes.map(r => `<tr><td>#${r.id}</td><td>${fmtDate(r.started_at)}</td><td>${esc(S.meta.site_status[r.result_status] || r.result_status || "")}</td><td>${r.confidence != null ? r.confidence.toFixed(2) : ""}</td></tr>`).join("")}</table></details>` : ""}
  </div>

  <div class="grid2">
    <div class="card">
      <h2>Contato comercial</h2>
      <div class="kv">
        <div>WhatsApp/celular</div><div>${c.whatsapp ? esc(c.whatsapp) : "—"}${c.whatsapp_link ? " · " + link(c.whatsapp_link, "link publicado") : ""}</div>
        <div>Telefone</div><div>${c.telefone ? esc(c.telefone) + ` <span class="muted">(fonte: ${esc(c.telefone_fonte || "")})</span>` : "—"}</div>
        <div>E-mail</div><div>${c.email ? esc(c.email) + ` <span class="muted">(fonte: ${esc(c.email_fonte || "")})</span>` : "—"}</div>
      </div>
      ${c.aviso ? `<div class="alert warn">${esc(c.aviso)}</div>` : ""}
    </div>
    <div class="card">
      <h2>Qualificação <span class="score">${q.na_campanha && q.score != null ? Math.round(q.score) + "/100" : ""}</span></h2>
      ${q.exclusao ? `<div class="alert warn">Fora da campanha: ${esc(q.exclusao)}</div>` : ""}
      <div class="bars">${Object.entries(comp).map(([k, v]) => `<div class="row"><span>${esc({ verificacao: "Site não localizado", aderencia: "Aderência ao serviço", contato: "Contato disponível", beneficio: "Benefício específico", evidencias: "Qualidade das evidências" }[k] || k)}</span><div class="bar"><span style="width:${(100 * v.pontos / v.max).toFixed(0)}%"></span></div><span>${v.pontos}/${v.max}</span></div><div class="muted small" style="margin:-2px 0 4px 178px">${esc(v.nota)}</div>`).join("")}</div>
    </div>
  </div>

  <div class="card">
    <h2>Resumo do negócio</h2>
    <p>${esc(d.resumo)}</p>
    <details ${d.fatos.length < 8 ? "open" : ""}><summary>Informações coletadas (${d.fatos.length}) — com fonte</summary>
    <table><tr><th>Tipo</th><th>Valor</th><th>Fonte</th><th></th></tr>${d.fatos.map(f => `<tr><td>${esc(f.tipo_label)}</td><td class="wrap">${esc(f.value)}${f.evidence ? `<div class="muted">“${esc(f.evidence)}”</div>` : ""}</td><td>${f.source_url ? link(f.source_url, f.source) : esc(f.source)}<div class="muted">${esc(f.method)}</div></td><td><button class="btn small danger" data-reject="${f.id}" title="Remover e não trazer de volta">✕</button></td></tr>`).join("")}</table></details>
    <h3>Adicionar material público</h3>
    <div class="muted small">Cole textos que você mesmo consultou (ex.: bio do Instagram, descrição no Google Maps), com o link. O Ollama extrai serviços e formas de contato, citando trechos literais.</div>
    <div class="form-row"><input id="mat-title" placeholder="Título (ex.: Bio do Instagram)"><input id="mat-url" class="grow" placeholder="Link de origem"></div>
    <textarea id="mat-text" style="min-height:80px" placeholder="Texto público"></textarea>
    <div class="toolbar"><button class="btn" id="mat-save">Salvar e analisar</button><button class="btn" id="enrich">Reanalisar fontes</button>
      ${d.materiais.length ? `<span class="muted small">${d.materiais.length} material(is) salvo(s)</span>` : ""}</div>
  </div>

  <div class="card">
    <h2>Possível aplicação de um site</h2>
    <div class="muted small">Possibilidades a avaliar na conversa — não afirmam necessidade.</div>
    ${apps.com_evidencia.length ? `<ul>${apps.com_evidencia.map(b => `<li><b>${esc(b.titulo)}</b> — ${esc(b.justificativa)}${b.evidencias && b.evidencias.length ? `<div class="muted small">Evidências: ${b.evidencias.map(esc).join(" · ")}</div>` : ""}</li>`).join("")}</ul>` : `<div class="muted">Nenhuma aplicação com evidência específica ainda.</div>`}
    ${apps.sugestoes_segmento.length ? `<details><summary>Comuns no segmento (sem evidência desta empresa)</summary><ul class="small">${apps.sugestoes_segmento.map(b => `<li>${esc(b.titulo)}</li>`).join("")}</ul></details>` : ""}
  </div>

  <div class="card">
    <h2>Mensagem inicial ${msg ? `<span class="badge s-${msg.status === "aprovada" ? "site_proprio" : msg.status === "bloqueada" ? "possivel_indisponivel" : "pendente"}">${esc(msg.status)}</span> <span class="muted small">v${msg.version} · ${esc(msg.generator)}</span>` : ""}</h2>
    <textarea id="msg-body" placeholder="Gere uma mensagem ou escreva a sua">${esc(msg ? msg.body : "")}</textarea>
    ${msg && msg.guardrails.bloqueios.length ? `<div class="alert bad">Não pode ser aprovada: ${msg.guardrails.bloqueios.map(esc).join("; ")}</div>` : ""}
    ${msg && msg.guardrails.avisos.length ? `<div class="alert warn">${msg.guardrails.avisos.map(esc).join("<br>")}</div>` : ""}
    <div class="toolbar">
      <button class="btn" id="gen-llm">Gerar com Ollama</button>
      <button class="btn" id="gen-tpl">Gerar com modelo padrão</button>
      <button class="btn" id="msg-save" ${msg ? "" : "disabled"}>Salvar edição</button>
      <button class="btn primary" id="msg-approve" ${msg && msg.status === "rascunho" ? "" : "disabled"}>Aprovar</button>
    </div>
    ${msg && msg.status === "aprovada" ? `<div class="alert ok">Aprovada em ${fmtDate(msg.approved_at)}. O envio é manual:
      <button class="btn small" id="copy">Copiar</button>
      ${d.whatsapp_envio ? `<a class="btn small" style="text-decoration:none" href="${esc(d.whatsapp_envio)}" target="_blank" rel="noopener">Abrir no WhatsApp</a>` : ""}
      ${c.email && c.email_fonte !== "cnpj" ? `<a class="btn small" style="text-decoration:none" href="mailto:${esc(c.email)}?subject=${encodeURIComponent("Site para " + L.name)}&body=${encodeURIComponent(msg.body)}">Abrir e-mail</a>` : ""}
      — depois marque como “Contatado”.</div>` : ""}
  </div>

  <div class="grid2">
    <div class="card">
      <h2>Funil</h2>
      <div class="form-row"><select id="funil">${funilOpts}</select><input id="funil-note" class="grow" placeholder="Nota (opcional)"><button class="btn" id="funil-save">Salvar</button></div>
    </div>
    <div class="card">
      <h2>Histórico</h2>
      <details><summary>${d.historico.length} registro(s)</summary>
      <table>${d.historico.map(h => `<tr><td>${fmtDate(h.changed_at)}</td><td>${esc(h.actor)}</td><td>${esc(h.field)}: ${esc(h.old_value ?? "—")} → ${esc(h.new_value ?? "—")}<div class="muted">${esc(h.note || "")}</div></td></tr>`).join("")}</table></details>
    </div>
  </div>`;

  const id = L.id;
  $("#verify").onclick = async e => { e.target.disabled = true; e.target.textContent = "Verificando…"; await act(api("POST", `/api/leads/${id}/verify`, {}), "Verificação concluída"); };
  $("#m-save").onclick = () => act(api("POST", `/api/leads/${id}/site-status`, { status: $("#m-status").value, url: $("#m-url").value, source: $("#m-src").value, note: $("#m-note").value }), "Correção salva");
  if ($("#clear-manual")) $("#clear-manual").onclick = () => act(api("POST", `/api/leads/${id}/site-status/clear`, {}), "Correção removida");
  if ($("#unchain")) $("#unchain").onclick = () => act(api("POST", `/api/leads/${id}/chain`, { is_chain: false, note: "revisado pelo usuário" }), "Atualizado");
  $("#mat-save").onclick = async e => { e.target.disabled = true; await act(api("POST", `/api/leads/${id}/materials`, { title: $("#mat-title").value, source_url: $("#mat-url").value, content: $("#mat-text").value }), "Material salvo e analisado"); };
  $("#enrich").onclick = async e => { e.target.disabled = true; const r = await act(api("POST", `/api/leads/${id}/enrich`, {})); if (r && r.enriquecimento) toast(r.enriquecimento.notas.join(" ") || `${r.enriquecimento.facts} informações`); };
  $("#gen-llm").onclick = async e => { e.target.disabled = true; e.target.textContent = "Gerando…"; await act(api("POST", `/api/leads/${id}/messages/generate`, { usar_ollama: true }), "Mensagem gerada — revise antes de aprovar"); };
  $("#gen-tpl").onclick = () => act(api("POST", `/api/leads/${id}/messages/generate`, { usar_ollama: false }), "Mensagem gerada — revise antes de aprovar");
  if (msg) {
    $("#msg-save").onclick = () => act(api("POST", `/api/messages/${msg.id}/edit`, { body: $("#msg-body").value }), "Nova versão salva");
    $("#msg-approve").onclick = () => act(api("POST", `/api/messages/${msg.id}/approve`, {}), "Mensagem aprovada");
    if ($("#copy")) $("#copy").onclick = () => navigator.clipboard.writeText(msg.body).then(() => toast("Copiado"));
  }
  $("#funil-save").onclick = () => act(api("POST", `/api/leads/${id}/pipeline`, { status: $("#funil").value, note: $("#funil-note").value }), "Funil atualizado");
  $("#detail").onclick = e => {
    const b = e.target.closest("[data-reject]");
    if (b && confirm("Remover esta informação? Ela não voltará em novas análises.")) act(api("POST", `/api/facts/${b.dataset.reject}/reject`, {}), "Removida");
  };
}

// ---------------------------------------------------------------- tarefas
async function startJob(path, body) {
  const job = await guarded(() => api("POST", path, body));
  if (!job) return;
  toast(`Tarefa #${job.id} iniciada`);
  pollJob(job.id);
}
async function pollJob(id) {
  const job = await api("GET", `/api/jobs/${id}`).catch(() => null);
  if (!job) return;
  const box = $("#jobs");
  if (box) renderJobs();
  if (job.status === "na_fila" || job.status === "executando") return setTimeout(() => pollJob(id), 2000);
  toast(job.status === "concluido" ? `Tarefa #${id} concluída` : `Tarefa #${id}: ${job.erro}`, 6000);
  loadHealth();
  if (["campanha", "verificar", "com_site", "todos"].includes(S.tab)) loadLeads();
  if (S.tab === "piloto" && S.pilotId) loadPilot(S.pilotId);
}
async function renderJobs() {
  const box = $("#jobs"); if (!box) return;
  const { jobs } = await api("GET", "/api/jobs");
  box.innerHTML = jobs.length ? `<table><tr><th>#</th><th>Tipo</th><th>Status</th><th>Progresso</th><th>Resultado</th></tr>${jobs.map(j => `<tr><td>${j.id}</td><td>${esc(j.tipo)}</td><td>${esc(j.status)}</td><td>${j.total ? `${j.progresso}/${j.total}` : ""}</td><td class="wrap small">${esc(j.erro || (j.resultado ? JSON.stringify(j.resultado) : ""))}${j.mensagens.length ? `<div class="muted">${j.mensagens.slice(-3).map(esc).join("<br>")}</div>` : ""}</td></tr>`).join("")}</table>` : `<div class="muted small">Nenhuma tarefa nesta sessão.</div>`;
}

// ---------------------------------------------------------------- descoberta
function renderDiscovery() {
  const r = S.meta.regioes;
  const segs = Object.entries(S.meta.segmentos).map(([k, v]) => `<label><input type="checkbox" name="seg" value="${k}"> ${esc(v)}</label>`).join("");
  main().innerHTML = `
  <div class="grid2">
    <div class="card">
      <h2>Buscar estabelecimentos (OpenStreetMap — gratuito)</h2>
      <div class="muted small">Prioriza quem não tem link de site na fonte, mas todos passam pela verificação antes de qualquer conclusão.</div>
      <h3>Segmentos</h3><div class="checks">${segs}</div>
      <h3>Região</h3>
      <div class="form-row"><label><input type="radio" name="rt" value="df" checked> ${esc(r.df)}</label></div>
      <div class="form-row"><label><input type="radio" name="rt" value="ra"> Região Administrativa/bairro do DF</label>
        <select id="ra">${r.regioes_administrativas.map(x => `<option>${esc(x)}</option>`).join("")}</select>
        <label>raio (m)</label><input id="raio" type="number" value="2500" style="width:90px"></div>
      <div class="form-row"><label><input type="radio" name="rt" value="municipio_go"> Município do entorno (GO)</label>
        <select id="mun">${r.entorno_go.map(x => `<option>${esc(x)}</option>`).join("")}</select></div>
      <div class="form-row"><label>Limite de resultados (opcional)</label><input id="lim" type="number" style="width:100px"></div>
      <button class="btn primary" id="go">Buscar</button>
    </div>
    <div class="card">
      <h2>Verificar em lote</h2>
      <div class="muted small">Executa verificação de site + pesquisa sem site + qualificação para leads da lista “A verificar” (pendentes primeiro, prioridade alta antes).</div>
      <div class="form-row"><label>Quantidade</label><input id="blim" type="number" value="25" style="width:90px">
        <select id="bst"><option value="pendente">Só pendentes</option><option value="">Toda a lista “A verificar”</option></select>
        <button class="btn" id="bgo">Iniciar</button></div>
      <h3>Adicionar lead manualmente</h3>
      <div class="form-row"><input id="n-name" class="grow" placeholder="Nome comercial *"><input id="n-phone" placeholder="Telefone"></div>
      <div class="form-row"><input id="n-hood" placeholder="Bairro/RA"><input id="n-city" placeholder="Cidade" value="Brasília">
        <select id="n-seg"><option value="">Segmento</option>${Object.entries(S.meta.segmentos).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></div>
      <div class="form-row"><input id="n-links" class="grow" placeholder="Links públicos (separados por espaço)"><button class="btn" id="n-save">Adicionar</button></div>
      <h3>Tarefas</h3><div id="jobs"></div>
    </div>
  </div>`;
  renderJobs();
  $("#go").onclick = () => {
    const segments = [...document.querySelectorAll("input[name=seg]:checked")].map(x => x.value);
    const rt = document.querySelector("input[name=rt]:checked").value;
    const region_name = rt === "ra" ? $("#ra").value : rt === "municipio_go" ? $("#mun").value : null;
    startJob("/api/discover", { segments, region_type: rt, region_name, radius: $("#raio").value, limit: $("#lim").value || null });
  };
  $("#bgo").onclick = () => startJob("/api/jobs/process", { view: "verificar", status: $("#bst").value, limit: +$("#blim").value || 25 });
  $("#n-save").onclick = async () => {
    const r = await guarded(() => api("POST", "/api/leads", { name: $("#n-name").value, phone: $("#n-phone").value, neighborhood: $("#n-hood").value, city: $("#n-city").value, segment: $("#n-seg").value, links: $("#n-links").value }));
    if (r) { toast(r.criado ? "Lead adicionado (pendente de verificação)" : "Já existia — dados mesclados"); loadHealth(); }
  };
}

// ---------------------------------------------------------------- piloto
async function renderPilot() {
  const { amostras } = await api("GET", "/api/pilot");
  main().innerHTML = `
  <div class="card">
    <h2>Validação do piloto</h2>
    <div class="muted small">Revise manualmente uma amostra aleatória de leads classificados automaticamente como “site próprio não localizado”. A ferramenta mede quantos tinham site e diagnostica cada erro — use isso para melhorar a verificação antes de aumentar o volume.</div>
    <div class="form-row" style="margin-top:8px"><label>Tamanho</label><input id="p-size" type="number" value="30" style="width:80px">
      <label><input type="checkbox" id="p-redes" checked> incluir “somente redes/plataformas”</label>
      <label>Semente (opcional)</label><input id="p-seed" type="number" style="width:110px">
      <button class="btn primary" id="p-new">Sortear amostra</button></div>
    ${amostras.length ? `<table><tr><th>#</th><th>Amostra</th><th>Criada</th><th>Revisados</th><th></th></tr>${amostras.map(a => `<tr><td>${a.id}</td><td>${esc(a.name)}</td><td>${fmtDate(a.created_at)}</td><td>${a.revisados || 0}/${a.itens}</td><td><button class="btn small" data-open="${a.id}">Abrir</button></td></tr>`).join("")}</table>` : ""}
  </div>
  <div id="pilot-box"></div>`;
  $("#p-new").onclick = async () => {
    const statuses = $("#p-redes").checked ? ["nao_localizado", "somente_redes"] : ["nao_localizado"];
    const s = await guarded(() => api("POST", "/api/pilot", { size: +$("#p-size").value, statuses, seed: $("#p-seed").value }));
    if (s) { S.pilotId = s.id; renderPilot(); }
  };
  main().onclick = e => { const b = e.target.closest("[data-open]"); if (b) loadPilot(+b.dataset.open); };
  if (S.pilotId) loadPilot(S.pilotId);
}
async function loadPilot(id) {
  S.pilotId = id;
  const [s, rep] = await Promise.all([api("GET", `/api/pilot/${id}`), api("GET", `/api/pilot/${id}/report`)]);
  const res = S.meta.revisao_piloto, how = S.meta.como_encontrou;
  const pct = v => v == null ? "—" : (100 * v).toFixed(1) + "%";
  const group = g => Object.entries(g).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.revisados}</td><td>${v.tinham_site}</td><td>${pct(v.taxa)}</td></tr>`).join("");
  $("#pilot-box").innerHTML = `
  <div class="grid2">
    <div class="card">
      <h2>Resultado — ${esc(s.name)}</h2>
      <div class="big">${pct(rep.taxa_site_nao_encontrado)}</div>
      <div class="muted">tinham site e a ferramenta não encontrou (${rep.tinham_site} de ${rep.revisados} revisados) · IC 95%: ${pct(rep.intervalo_95[0])}–${pct(rep.intervalo_95[1])}</div>
      <div class="small muted">${rep.pendentes} pendente(s), ${rep.inconclusivos} inconclusivo(s) · semente ${rep.amostra.semente} · universo ${esc(rep.amostra.criterios.universo)}</div>
      <div class="alert ${rep.decisao.pode_escalar ? "ok" : "warn"}">${rep.decisao.pode_escalar ? "Pode aumentar o volume." : "Ainda não aumente o volume."} ${esc(rep.decisao.motivo)}</div>
      ${rep.recomendacoes.length ? `<h3>Melhorias sugeridas</h3><ul class="small">${rep.recomendacoes.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      ${rep.erros.length ? `<h3>Sites não encontrados</h3><table><tr><th>Empresa</th><th>Site</th><th>Como achou</th><th>Diagnóstico</th></tr>${rep.erros.map(x => `<tr><td>${esc(x.nome)}</td><td class="wrap">${link(x.url)}</td><td>${esc(x.como_encontrou)}</td><td>${esc(x.diagnostico)}</td></tr>`).join("")}</table>
        <div class="toolbar" style="margin-top:8px"><button class="btn" id="regress">Reverificar esses casos (após ajustes)</button></div>` : ""}
    </div>
    <div class="card">
      <h2>Recortes</h2>
      <h3>Por classificação</h3><table><tr><th></th><th>Revisados</th><th>Tinham site</th><th>Taxa</th></tr>${group(rep.por_classificacao)}</table>
      <h3>Por confiança</h3><table><tr><th></th><th>Revisados</th><th>Tinham site</th><th>Taxa</th></tr>${group(rep.por_confianca)}</table>
      <h3>Por segmento</h3><table><tr><th></th><th>Revisados</th><th>Tinham site</th><th>Taxa</th></tr>${group(rep.por_segmento)}</table>
    </div>
  </div>
  <div class="card">
    <h2>Revisão manual (${s.items.length} itens)</h2>
    <div class="muted small">Para cada empresa, pesquise você mesmo (links ao lado). Se encontrar um site próprio, registre a URL e como encontrou — isso vira correção humana no lead.</div>
    <table><tr><th>Empresa</th><th>Classificação na amostra</th><th>Pesquisar</th><th>Revisão</th></tr>
    ${s.items.map(i => {
      const q = encodeURIComponent(`"${i.name}" ${i.neighborhood || i.city || "Brasília"}`);
      return `<tr data-item="${i.id}"><td><b>${esc(i.name)}</b><div class="muted small">${esc([i.neighborhood, i.city].filter(Boolean).join(", "))}</div></td>
        <td>${esc(S.meta.site_status[i.status_at_sampling])}<div class="muted small">confiança ${i.confidence_at_sampling != null ? i.confidence_at_sampling.toFixed(2) : "—"}</div></td>
        <td class="links small"><a target="_blank" rel="noopener" href="https://www.google.com/search?q=${q}">Google</a><a target="_blank" rel="noopener" href="https://www.bing.com/search?q=${q}">Bing</a><a target="_blank" rel="noopener" href="https://www.google.com/maps/search/${q}">Maps</a></td>
        <td><div class="form-row"><select class="r-res"><option value="">—</option>${Object.entries(res).map(([k, v]) => `<option value="${k}" ${i.review_result === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
          <input class="r-url grow" placeholder="URL encontrada" value="${esc(i.found_url || "")}">
          <select class="r-how"><option value="">Como encontrou</option>${Object.entries(how).map(([k, v]) => `<option value="${k}" ${i.how_found === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
          <input class="r-note" placeholder="Nota" value="${esc(i.note || "")}"><button class="btn small r-save">Salvar</button></div>
          ${i.reviewed_at ? `<div class="muted small">revisado ${fmtDate(i.reviewed_at)}</div>` : ""}</td></tr>`;
    }).join("")}</table>
  </div>`;
  $("#pilot-box").onclick = async e => {
    if (e.target.id === "regress") return startJob(`/api/pilot/${id}/regress`, {});
    const b = e.target.closest(".r-save"); if (!b) return;
    const tr = b.closest("tr");
    const r = await guarded(() => api("POST", `/api/pilot/items/${tr.dataset.item}`, { result: $(".r-res", tr).value, found_url: $(".r-url", tr).value, how_found: $(".r-how", tr).value, note: $(".r-note", tr).value }), "Revisão salva");
    if (r) loadPilot(id);
  };
}

// ---------------------------------------------------------------- configurações
async function renderConfig() {
  const s = await api("GET", "/api/settings");
  const models = (S.health && S.health.ollama.modelos_instalados) || [];
  const f = (k, label, help = "", type = "text") => `<div class="form-row"><label style="min-width:230px">${esc(label)}</label><input data-k="${k}" type="${type}" class="grow" value="${esc(Array.isArray(s[k]) ? s[k].join(", ") : s[k])}">${help ? `<span class="muted small">${esc(help)}</span>` : ""}</div>`;
  main().innerHTML = `
  <div class="grid2">
    <div class="card">
      <h2>Quem está prospectando</h2>
      ${f("vendedor_nome", "Seu nome (assina as mensagens)")}
      ${f("vendedor_empresa", "Sua empresa (opcional)")}
      ${f("vendedor_cidade", "Sua cidade")}
      ${f("servico_descricao", "Serviço oferecido")}
      ${f("segmentos_alvo", "Segmentos-alvo (chaves, vírgula)", "vazio = todos")}
      <div class="muted small">Chaves: ${Object.keys(S.meta.segmentos).map(esc).join(", ")}</div>
    </div>
    <div class="card">
      <h2>Análise local (Ollama)</h2>
      ${f("ollama_url", "Endereço do Ollama")}
      <div class="form-row"><label style="min-width:230px">Modelo</label><input data-k="ollama_model" list="models" class="grow" value="${esc(s.ollama_model)}"><datalist id="models">${models.map(m => `<option value="${esc(m)}">`).join("")}</datalist></div>
      <div class="muted small">Instalados: ${models.length ? models.map(esc).join(", ") : "nenhum encontrado (o Ollama está rodando?)"}</div>
      <h2 style="margin-top:16px">Busca web (verificação)</h2>
      <div class="form-row"><label style="min-width:230px">Provedor</label><select data-k="busca_provedor">${["nenhum", "searxng", "brave"].map(p => `<option ${s.busca_provedor === p ? "selected" : ""}>${p}</option>`).join("")}</select></div>
      ${f("searxng_url", "SearXNG local")}
      ${f("brave_api_key", "Chave Brave Search API", "fica só neste computador", "password")}
      ${f("busca_min_consultas", "Mínimo de consultas para concluir", "", "number")}
      ${f("busca_max_candidatos", "Máx. candidatos por lead", "", "number")}
      <div class="form-row"><label style="min-width:230px">Testar nome.com.br / nome.com</label><input type="checkbox" data-k="adivinhar_dominio" ${s.adivinhar_dominio ? "checked" : ""}><span class="muted small">só aceito com evidência de conteúdo</span></div>
    </div>
    <div class="card">
      <h2>Rede e fontes</h2>
      ${f("http_timeout", "Tempo limite (s)", "", "number")}
      ${f("intervalo_por_host", "Intervalo entre acessos ao mesmo site (s)", "", "number")}
      ${f("overpass_url", "Overpass API")}
      ${f("nominatim_url", "Nominatim")}
    </div>
    <div class="card">
      <h2>Piloto</h2>
      ${f("piloto_limite_erro", "Limite aceitável de site não encontrado", "ex.: 0.10 = 10%", "number")}
      ${f("piloto_minimo_revisados", "Mínimo de itens revisados", "", "number")}
    </div>
  </div>
  <button class="btn primary" id="save-cfg">Salvar configurações</button>`;
  $("#save-cfg").onclick = async () => {
    const body = {};
    document.querySelectorAll("[data-k]").forEach(el => { body[el.dataset.k] = el.type === "checkbox" ? el.checked : el.value; });
    if (await guarded(() => api("POST", "/api/settings", body), "Configurações salvas")) loadHealth();
  };
}

// ---------------------------------------------------------------- início
(async function init() {
  S.meta = await api("GET", "/api/meta");
  await loadHealth();
  render();
})();
