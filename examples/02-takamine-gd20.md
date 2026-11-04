# Preço do Takamine GD20

**O que mostra:** consulta de preço e estoque de um produto específico, e parcelamento.

*Gerada por `scripts/run_examples.py` com o agente de verdade. Data e hora simuladas: sexta-feira, 20/02/2026, 10:00 (horário de Campo Grande).*

---

**Cliente:** Quanto custa o Takamine GD20?

> `tool: search_products(query="Takamine GD20", category="Violões")`

**Agente:** O Takamine GD20 Dreadnought Natural está disponível por R$ 2.199,00. Quer que eu consulte as opções de pagamento também?

---

**Cliente:** Tem em estoque? Dá pra parcelar sem juros?

> `tool: search_products(query="Takamine GD20", category="Violões")`
>
> `tool: get_price_quote(product_id=95, quantity=1)`

**Agente:** Sim! Temos 5 unidades em estoque. Dá para parcelar em até 12x sem juros de R$ 183,25 no cartão. O frete não foi calculado; se quiser consultar, me diga sua cidade.
