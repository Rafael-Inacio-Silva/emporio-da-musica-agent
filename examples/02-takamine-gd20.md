# Preço do Takamine GD20

**O que mostra:** consulta de preço e estoque de um produto específico, e parcelamento.

*Gerada por `scripts/run_examples.py` com o agente de verdade. Data e hora simuladas: sexta-feira, 20/02/2026, 10:00 (horário de Campo Grande).*

---

**Cliente:** Quanto custa o Takamine GD20?

> `tool: search_products(query="Takamine GD20", category="Violões", brand="Takamine")`

**Agente:** Oi! Eu sou a Lia, assistente virtual da Empório da Música. O Takamine GD20 Dreadnought Natural custa R$ 2.199,00 e está disponível. Posso consultar as formas de pagamento e o frete para você?

---

**Cliente:** Tem em estoque? Dá pra parcelar sem juros?

> `tool: get_price_quote(product_id=95, quantity=1)`

**Agente:** Sim, tem 5 unidades em estoque. Dá para parcelar sem juros em até 12x de R$ 183,25 no cartão. O frete é calculado à parte. Posso ajudar com mais alguma coisa?
