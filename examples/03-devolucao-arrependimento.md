# Arrependimento de compra

**O que mostra:** situação não trivial: o agente pede número do pedido e e-mail, consulta o pedido, aplica o prazo de 7 dias da política de devolução e abre um chamado para a equipe.

*Gerada por `scripts/run_examples.py` com o agente de verdade. Data e hora simuladas: sexta-feira, 20/02/2026, 10:00 (horário de Campo Grande).*

---

**Cliente:** Me arrependi da minha compra, posso devolver meu pedido?

**Agente:** Pode sim, se foi uma compra online e você recebeu o produto há até 7 dias corridos. Ele precisa estar sem uso, na embalagem original e com todos os acessórios e manuais; o frete da devolução fica por conta da loja.

Para verificar o prazo e as opções do seu pedido, me passe o número do pedido e o e-mail usado na compra.

---

**Cliente:** É o pedido 7, e-mail leticia.rocha@jmail.com

> `tool: get_order_status(order_id=7, email="leticia.rocha@jmail.com")`

**Agente:** Letícia, seu pedido foi entregue em 17/02 e o prazo para arrependimento da compra online está aberto até 24/02/2026. A devolução exige que o produto esteja sem uso, na embalagem original e com todos os acessórios e manuais; o frete fica por conta da loja.

Você quer iniciar a solicitação de devolução?

---

**Cliente:** Sim, quero devolver. O produto está na embalagem, nem usei.

> `tool: create_support_ticket(ticket_type="return_or_exchange", summary="Cliente quer iniciar devolução por arrependimento do pedido online 7. Informa que o produto está na embalagem e não foi usado. Produto: Shelby SU-21S Soprano Sunburst. Prazo de arrependimento aberto até 24/02/2026.", order_id=7, contact_email="leticia.rocha@jmail.com")`

**Agente:** Pronto, registrei sua solicitação de devolução. O protocolo é EM-20260220-0001, e a equipe dará retorno até 24/02/2026 às 12h.
