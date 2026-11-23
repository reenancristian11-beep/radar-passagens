# ✈️ Radar de Passagens

Procura sozinho promoções de passagens aéreas **ida e volta** saindo de
**SJP, GRU, VCP e CGH** para **86 destinos no mundo todo**, em **econômica,
premium economy, executiva e primeira classe**, olhando **todas as datas dos
próximos ~8 meses**. Quando acha uma promoção, manda alerta no **Telegram**
e/ou **e-mail** e mostra tudo numa página online.

**Custo: zero.** Roda no GitHub Actions e o site fica no GitHub Pages (grátis
em repositório público). Os preços vêm do Google Flights, que reúne quase todas
as companhias aéreas e agências, através da biblioteca open source
[fli](https://github.com/punitarani/fli) — sem chave de API.

## Como funciona

1. A cada 30 minutos o GitHub roda `python -m radar`.
2. Cada rodada pesquisa 20 combinações origem × destino × classe (são 1.076 no
   total, então a lista inteira é revisada a cada ~2 dias, em rodízio).
3. Para cada combinação, o Google Flights devolve o preço mais barato de cada
   data de partida. O radar pega a data mais barata e decide se é promoção:
   - **preço-alvo**: abaixo do valor fixo da região/classe no `config.yaml`;
   - **queda**: 20% abaixo do preço habitual (mediana das varreduras anteriores);
   - **tarifa erro**: 40% abaixo da média das outras datas da mesma rota.
4. Promoções novas viram alerta (sem repetir a mesma promoção por 7 dias, a não
   ser que o preço caia mais 5%).
5. Os preços ficam salvos em `data/` e o site é atualizado.

## Instalação (uns 15 minutos)

### 1. Criar o repositório
1. Crie uma conta grátis em <https://github.com> (se ainda não tiver).
2. Crie um repositório **público** (ex.: `radar-passagens`) e envie estes arquivos.
   > Precisa ser público: assim o GitHub Actions é ilimitado e o GitHub Pages é grátis.
   > Seus tokens ficam protegidos nos *Secrets*, ninguém vê.

### 2. Ligar o site
*Settings → Pages → Build and deployment → Source:* **GitHub Actions**.

O endereço será `https://SEU-USUARIO.github.io/radar-passagens/`.

### 3. Alertas no Telegram (recomendado)
1. No Telegram, fale com **@BotFather** → `/newbot` → escolha um nome. Ele te dá
   um **token** (ex.: `123456:ABC...`).
2. Mande qualquer mensagem para o seu bot novo.
3. Abra no navegador `https://api.telegram.org/botSEU_TOKEN/getUpdates` e copie
   o número em `"chat":{"id": ...}`.
4. No GitHub: *Settings → Secrets and variables → Actions → New repository secret*:
   - `TELEGRAM_BOT_TOKEN` = o token
   - `TELEGRAM_CHAT_ID` = o número do chat

### 4. Alertas por e-mail (opcional)
Com Gmail: ative a verificação em duas etapas e crie uma **senha de app** em
<https://myaccount.google.com/apppasswords>. Depois crie os secrets:
- `SMTP_USUARIO` = seu e-mail do Gmail
- `SMTP_SENHA` = a senha de app (16 letras)
- `EMAIL_PARA` = para onde mandar (se vazio, manda para você mesmo)

### 5. Rodar a primeira vez
*Actions → Radar de passagens → Run workflow.* Depois disso ele roda sozinho a
cada 30 minutos.

## ⭐ Destinos prioritários

No topo do `config.yaml` (dá para editar direto no GitHub, no ícone de lápis):

```yaml
prioridades:
  destinos: [Lisboa, Orlando, NAP]
```

- Use o nome da cidade como está na lista de destinos (`Lisboa`) ou o código
  do aeroporto (`LIS`). Um aeroporto fora da lista (ex.: `NAP`, Nápoles) é
  adicionado automaticamente.
- Destinos a até `raio_proximos_km` (padrão 600 km) também viram prioridade:
  Lisboa puxa Porto e Madri; Orlando puxa Miami.
- Metade de cada rodada vai para os prioritários, que são pesquisados primeiro.
  Na prática eles são revisados a cada poucas horas, em vez de a cada ~2 dias.
- Prioritários avisam com preço até 15% acima do preço-alvo, chegam com
  ⭐ no Telegram e ficam no topo do site (com o filtro "⭐ Só prioridades").
- Os outros destinos continuam sendo pesquisados e avisados normalmente.

## 🧳 Upgrade em passagens já compradas

Cadastre suas viagens em `viagens_compradas` no `config.yaml` (há um exemplo
comentado lá). A cada 6 horas o radar confere o preço **da mesma viagem**
(mesmas datas e, se você informar, os mesmos números de voo) em todas as classes
acima da sua e avisa no Telegram quando:

- ⬆️ **Upgrade:** a classe de cima custa até `avisar_upgrade_ate` reais a mais do
  que você pagou. Aí vale pedir o upgrade pago à companhia ou remarcar pagando
  a diferença (depende das regras da sua tarifa).
- 💸 **Sua passagem baixou:** a sua própria classe ficou 10% ou mais barata —
  útil para remarcar ou pedir crédito, se a tarifa permitir.

O site mostra uma tabela "Minhas viagens compradas" com o preço de hoje de cada
classe. **Limitação:** o radar não acessa a sua reserva, então não vê ofertas
exclusivas de upgrade por lance ou com milhas — essas só aparecem no app/site da
companhia aérea.

## Personalizar (`config.yaml`)

| O que | Onde |
|---|---|
| Destinos prioritários | `prioridades` |
| Viagens já compradas (upgrades) | `viagens_compradas` |
| Aeroportos de origem | `origens` |
| Destinos (código IATA + cidade) | `destinos` |
| Classes por região e **preço-alvo** de cada uma | `regioes` |
| Quantos dias de viagem | `regioes → duracao_dias` |
| Quantos meses à frente olhar | `datas → horizonte_dias` |
| Sensibilidade das promoções | `promocao` |
| Velocidade do rodízio | `varredura → combinacoes_por_execucao` |

Os preços-alvo iniciais são estimativas de "preço bom" saindo de São Paulo.
Depois de uma ou duas semanas, olhe o site (desligue o filtro "Só promoções")
para ver os preços reais e ajuste.

## Rodar no seu computador (opcional)

```bash
pip install -r requirements.txt
```

```bash
python -m radar --rota GRU-LIS-executiva --sem-alertas
```

```bash
python -m pytest -q
```

## Limitações (leia!)

- **Não é API oficial.** O fli lê o Google Flights do mesmo jeito que o site. O
  Google pode mudar algo e a busca parar até a biblioteca ser atualizada
  (atualize a versão em `requirements.txt`). O Google também pode limitar as
  consultas vindas dos servidores do GitHub; se isso acontecer, o radar para a
  rodada sozinho e tenta de novo depois. Use para fins pessoais.
- **Sempre confira o preço final** no Google Flights / site da companhia antes de
  comprar. Os valores são de 1 adulto, ida e volta, sem garantia de bagagem.
- Não cobre passagens com **milhas** (só dinheiro).
- "Destino não importa" é atendido por uma lista grande de destinos — o Google
  não permite consultar "qualquer lugar" de graça. Adicione os que quiser.
- O GitHub pausa tarefas agendadas de repositórios sem atividade por 60 dias; os
  commits automáticos de preços mantêm o repositório ativo.
