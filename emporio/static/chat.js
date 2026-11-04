/** Chat page: sends each message to the local server and shows the answer. */

const conversa = document.getElementById('conversa');
const inicio = document.getElementById('inicio');
const formulario = document.getElementById('formulario');
const campo = document.getElementById('mensagem');
const enviar = document.getElementById('enviar');
const chaveFerramentas = document.getElementById('mostrar-ferramentas');
const novaConversa = document.getElementById('nova-conversa');
const avisoData = document.getElementById('data-referencia');

const CHAVE_PREFERENCIA = 'emporio:mostrar-ferramentas';

// One id per tab: the server keeps a separate history for each one.
let conversationId = crypto.randomUUID();

function lerPreferencia() {
  try { return localStorage.getItem(CHAVE_PREFERENCIA) === '1'; } catch { return false; }
}

function salvarPreferencia(ligado) {
  try { localStorage.setItem(CHAVE_PREFERENCIA, ligado ? '1' : '0'); } catch { /* storage blocked */ }
}

function aplicarFerramentas(ligado) {
  chaveFerramentas.checked = ligado;
  document.body.classList.toggle('mostrar-ferramentas', ligado);
}

function rolarParaFim() {
  conversa.scrollTop = conversa.scrollHeight;
}

// textContent, never innerHTML: the answer is shown as plain text.
function adicionarMensagem(autor, texto, { ferramentas = [], erro = false } = {}) {
  inicio.hidden = true;
  const item = document.createElement('div');
  item.className = `mensagem ${autor}${erro ? ' erro' : ''}`;
  if (ferramentas.length) {
    const lista = document.createElement('ul');
    lista.className = 'ferramentas';
    for (const chamada of ferramentas) {
      const linha = document.createElement('li');
      linha.textContent = chamada;
      lista.append(linha);
    }
    item.append(lista);
  }
  const balao = document.createElement('div');
  balao.className = 'balao';
  balao.textContent = texto;
  item.append(balao);
  conversa.append(item);
  rolarParaFim();
}

function mostrarDigitando() {
  const item = document.createElement('div');
  item.className = 'mensagem agente digitando';
  item.setAttribute('aria-label', 'Digitando');
  item.innerHTML = '<div class="balao"><span></span><span></span><span></span></div>';
  conversa.append(item);
  rolarParaFim();
  return item;
}

function ocupado(sim) {
  campo.disabled = sim;
  enviar.disabled = sim;
  if (!sim) campo.focus();
}

async function mandar(texto) {
  adicionarMensagem('cliente', texto);
  ocupado(true);
  const digitando = mostrarDigitando();
  try {
    const resposta = await fetch('/api/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: conversationId, message: texto }),
    });
    const dados = await resposta.json().catch(() => ({}));
    digitando.remove();
    if (!resposta.ok) {
      adicionarMensagem('agente', dados.error || 'Não consegui processar a mensagem.', { erro: true });
      return;
    }
    adicionarMensagem('agente', dados.reply, { ferramentas: dados.tool_calls, erro: dados.failed });
  } catch {
    digitando.remove();
    adicionarMensagem('agente', 'Não consegui falar com o servidor. Ele ainda está rodando?', { erro: true });
  } finally {
    ocupado(false);
  }
}

function ajustarAltura() {
  campo.style.height = 'auto';
  campo.style.height = `${campo.scrollHeight}px`;
}

formulario.addEventListener('submit', (evento) => {
  evento.preventDefault();
  const texto = campo.value.trim();
  if (!texto || campo.disabled) return;
  campo.value = '';
  ajustarAltura();
  mandar(texto);
});

// Enter sends; Shift+Enter breaks the line.
campo.addEventListener('keydown', (evento) => {
  if (evento.key === 'Enter' && !evento.shiftKey && !evento.isComposing) {
    evento.preventDefault();
    formulario.requestSubmit();
  }
});
campo.addEventListener('input', ajustarAltura);

for (const sugestao of document.querySelectorAll('.sugestao')) {
  sugestao.addEventListener('click', () => mandar(sugestao.textContent));
}

chaveFerramentas.addEventListener('change', () => {
  aplicarFerramentas(chaveFerramentas.checked);
  salvarPreferencia(chaveFerramentas.checked);
});

novaConversa.addEventListener('click', () => {
  conversationId = crypto.randomUUID();
  for (const item of conversa.querySelectorAll('.mensagem')) item.remove();
  inicio.hidden = false;
  campo.value = '';
  ajustarAltura();
  campo.focus();
});

async function carregarInfo() {
  try {
    const dados = await (await fetch('/api/info')).json();
    if (dados.reference_date) {
      avisoData.textContent = `Demonstração com data de referência: ${dados.reference_date}`;
      avisoData.hidden = false;
    }
  } catch { /* the notice is optional */ }
}

aplicarFerramentas(lerPreferencia());
carregarInfo();
campo.focus();
