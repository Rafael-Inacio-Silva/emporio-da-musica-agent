# Violões até R$ 1.000

**O que mostra:** consulta ao catálogo com filtro de preço, depois preço no PIX, parcelamento e frete de um modelo.

*Gerada por `scripts/run_examples.py` com o agente de verdade. Data e hora simuladas: sexta-feira, 20/02/2026, 10:00 (horário de Campo Grande).*

---

**Cliente:** Quais opções de violões disponíveis custando até R$1000?

> `tool: search_products(category="Violões", max_price=1000)`

**Agente:** Temos estas opções disponíveis por até R$ 1.000:

- Tagima Memphis AC-39 Nylon Natural — R$ 429,90
- Rozini RC-104 Nylon Estudante — R$ 459,90
- Shelby SN-100 Folk Aço Preto — R$ 549,00
- Giannini GF-1R Aço Folk Sunburst — R$ 579,90
- Yamaha C40 Nylon Natural — R$ 599,90
- Giannini GN-15 Nylon Cedr Natural — R$ 689,00
- Yamaha F310 Aço Natural — R$ 699,90
- Tagima Dallas Tuner Aço Natural — R$ 749,00
- Shelby SGD-195E Elétrico Aço Sunburst — R$ 799,00
- Yamaha C70 Nylon Natural — R$ 849,00

Tem opções de nylon, aço e até eletroacústico. Quer que eu te ajude a escolher pelo estilo que você pretende tocar?

---

**Cliente:** Gostei do Yamaha C40. Quanto fica no PIX? E em quantas vezes posso parcelar no cartão? Moro em Campo Grande.

> `tool: search_products(query="Yamaha C40", category="Violões")`
>
> `tool: get_price_quote(product_id=81, quantity=1, city="Campo Grande")`

**Agente:** O Yamaha C40 fica R$ 569,91 no PIX. No cartão, dá para parcelar em até 6x de R$ 99,98 sem juros.

Para Campo Grande, o frete está grátis, com entrega estimada em 1 a 3 dias úteis.
