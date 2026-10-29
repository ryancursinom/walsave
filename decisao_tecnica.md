# Decisão técnica — Etapa 8: o juiz e a escrita

> **Invariante da prática:** se o turno for recusado, nenhuma escrita desse turno permanece — criação ou alteração, no financeiro e na agenda.

---

## 1. A decisão tomada, e por quê

### 1.1 O defeito

Até a Etapa 7, as três tools de escrita confirmavam o efeito **antes** do veredito:

| Tool | Efeito no banco | Onde estava o `commit()` |
|---|---|---|
| `add_transaction` | `INSERT` em `transactions` | dentro da tool, antes de devolver o resultado |
| `update_transaction` | `UPDATE` em `transactions` | dentro da tool, antes do `SELECT` de retorno |
| `add_event` | `INSERT` em `events` | dentro da tool, antes de devolver o resultado |

Cada tool abria a própria conexão (`psycopg2.connect`), dava `commit()` e fechava. O `rollback()` existente cobria apenas exceção SQL antes do commit. Quando o juiz recusava a frase, a conexão daquela tool já tinha sido fechada e a linha continuava no Postgres.

### 1.2 A fronteira escolhida: um bloco transacional por turno

**Família: Transacionar.** O turno inteiro compartilha uma única unidade de trabalho no PostgreSQL. A fronteira que autoriza a escrita a permanecer passou a ser **o veredito do JEV final**, avaliado em `executar_fluxo` depois de o grafo terminar:

- JEV aprovou **e** nenhuma tool teve falha SQL → `COMMIT` do turno;
- JEV recusou, JEV sem resposta, tool com falha SQL ou exceção no grafo → `ROLLBACK` do turno.

Como foi encaixado sem mudar a lógica de negócio das tools:

1. `executar_fluxo` cria um `TurnoTransacional` e o publica no `ContextVar CURRENT_TURNO` (mesmo mecanismo já usado por `CURRENT_USER_ID`).
2. `get_conn()` passa a verificar esse contexto. Dentro de um turno do chat, ela devolve um `_ConexaoDoTurno` sobre a conexão única do turno; fora de um turno (servidor MCP, scripts), devolve `psycopg2.connect` como antes.
3. Em `_ConexaoDoTurno`, cada abertura cria um `SAVEPOINT`. O `commit()` chamado pela tool vira `RELEASE SAVEPOINT`, o `rollback()` vira `ROLLBACK TO SAVEPOINT` e o `close()` libera a conexão para a próxima tool. **As tools continuam chamando `commit()`, `rollback()` e `close()` exatamente como antes** — o SQL, as validações e os retornos não mudaram, e `financial_tools.py` e `calendar_tools.py` não foram editados - SABOR COMMIT.
4. O nó `juiz` passa a devolver `jev_aprovado` (`True` só quando `choice == "apoiada"` e `noul ≥ 0,70`). `executar_fluxo` chama `turno.finalizar(confirmar=jev_aprovado)`.
5. Se o turno teve escrita e ela não ficou, a resposta ao usuário é trocada por uma frase que diz isso: *"Não consegui confirmar essa operação com segurança, por isso nada foi salvo. Pode repetir o pedido com os detalhes?"*. O estado do LangGraph recebe a mesma frase, para o histórico do próximo turno não afirmar um registro que não existe.

### 1.3 Por que essa fronteira cabe neste grafo

- **Todos os efeitos protegidos estão no mesmo banco.** `transactions` e `events` estão no mesmo PostgreSQL, então um único `COMMIT`/`ROLLBACK` cobre as três tools de uma vez. O rollback é nativo e atômico, e não há uma segunda operação que possa falhar.
- **O ponto de decisão já existe.** O juiz é o último nó em todos os caminhos que chegam a uma tool de escrita (`financeiro`/`agenda → orquestrador → guardrail_saida → juiz → END`). O commit fica logo depois dele, em `executar_fluxo`, sem nó novo nem agente novo.
- **As leituras do turno enxergam a escrita pendente.** Como todas as tools do turno usam a mesma conexão, um `search_transactions` depois de um `add_transaction` no mesmo turno vê a linha ainda não confirmada. Com uma conexão por tool, isso não aconteceria.
- **O SAVEPOINT preserva o comportamento de cada tool.** Se uma tool tem erro SQL, só o trecho dela é desfeito e a conexão volta a aceitar comandos; as tools seguintes do turno continuam funcionando. O erro fica registrado em `turno.falhas` e força o `ROLLBACK` final, como o grupo decidiu ("se qualquer parte tiver problema, desfaz").
- **Tools paralelas.** O `ToolNode` do LangGraph pode executar duas tool calls do mesmo turno em threads diferentes. Um `RLock` no turno serializa o uso da conexão, para que os savepoints não se intercalem.

### 1.4 Garantia em cada tool

| Tool | Antes | Depois |
|---|---|---|
| `add_transaction` | `INSERT` + `commit()` imediato | `INSERT` dentro do savepoint; a linha só existe para outros clientes após o `COMMIT` do turno |
| `update_transaction` | `UPDATE` + `commit()` + `SELECT` | `UPDATE` dentro do savepoint; o `SELECT` de retorno lê a versão pendente; recusa restaura o registro anterior |
| `add_event` | `INSERT` + `commit()` imediato | igual a `add_transaction`, na tabela `events` |

### 1.5 Fluxo de antes e de depois

**Antes**

```text
modelo escolhe a tool
  → tool grava e dá COMMIT  (conexão própria, fechada aqui)   ← efeito permanente
  → orquestrador redige a frase
  → guardrail de saída  → salvar_mensagem (Mongo, frase ainda não julgada)
  → juiz pode trocar a frase
  → chat mostra o texto seguro, mas a linha continua no banco
```

**Depois**

```text
executar_fluxo cria TurnoTransacional (CURRENT_TURNO)
  → guardrail de entrada
  → roteador JEV (critérios do prompt do roteador; contingência: roteador LLM)
  → especialista → tool: get_conn() = conexão do turno
        SAVEPOINT tool_n → SQL da tool → commit() da tool = RELEASE SAVEPOINT
  → orquestrador → guardrail de saída
  → juiz (JEV) → jev_aprovado
  → executar_fluxo:
        aprovado e sem falha SQL → COMMIT
        qualquer outro caso      → ROLLBACK (+ frase "nada foi salvo" se houve escrita)
  → salvar_mensagem (Mongo) com a frase que o usuário vê
  → fechar_turno (monitor) com rota e status reais
```

### 1.6 Decisão complementar: o roteador passa a ser o JEV

O grupo também decidiu que o roteador passa a ser um JEV usando os mesmos critérios do prompt. Isso pertence à família **Interceptar**: o JEV julga a mensagem antes que um agente com tools de escrita seja chamado.

- `_julgar_rota` (em `app/juiz.py`) envia ao JEV o texto de `ROUTER_PROMPT` (a mesma fonte do prompt do roteador, importada, não copiada), a mensagem atual e as últimas falas da conversa. Ela faz uma pergunta `choice` com seis opções: `financeiro`, `agenda`, `faq`, `saudacao`, `esclarecimento` e `fora_de_escopo`, cujos critérios reproduzem as regras 2 a 6 do prompt.
- As três primeiras opções encaminham ao especialista. As outras três respondem com as frases dos exemplos do próprio prompt (`ROUTER_SHOT_1`, `ROUTER_SHOT_2` e a regra 6, que pede uma pergunta de esclarecimento).
- **Contingência:** sem chave, com erro HTTP, timeout (5 s) ou com uma escolha desconhecida, o nó chama o roteador LLM original (`router_app`, sem nenhuma alteração), para que o chat não pare.

**Limite honesto:** o roteador JEV **não** é o que garante a invariante. Ele reduz encaminhamentos errados para agentes que escrevem, mas não vê a frase final nem os argumentos da tool. Quem garante a invariante é a transação do turno. A troca do roteador é a única mudança fora da fronteira estrita "veredito ↔ escrita". Ela foi pedida pelo grupo, não cria agente novo (substitui uma chamada LLM por uma chamada JEV) e não altera rota HTTP nem API.

---

## 2. As decisões que o grupo não tomou, e por quê

### 2.1 Redis como área de preparação ("prepared statement")

- **Como funcionaria aqui:** `add_transaction`, `update_transaction` e `add_event` gravariam a intenção em Redis (chave por turno, com TTL). Depois do JEV aprovar, um passo novo leria as chaves e executaria o SQL no Postgres; na recusa, as chaves expirariam ou seriam apagadas.
- **Vantagem real:** nada chega ao Postgres antes do veredito, e o TTL limpa sozinho o que foi recusado, sem ocupar a memória indefinidamente.
- **Custo real:**
  - as três tools precisariam ser reescritas para gravar em Redis, o que muda a lógica das tools e viola uma restrição do projeto;
  - `update_transaction` precisa localizar o registro (`match_text` + `date_local`) no Postgres, o que divide a lógica entre dois armazenamentos;
  - leituras do mesmo turno (`search_transactions`, `saldo_total`) não veriam a escrita preparada sem mesclar Redis e SQL;
  - a efetivação final ainda é uma escrita SQL que pode falhar depois de a frase "registrei" ter sido aprovada;
  - é mais uma dependência externa, com rede e custo.
- **Por que ficou de fora:** o ganho de latência no armazenamento (sub-milissegundo) desaparece diante da chamada ao JEV (centenas de ms a segundos), e a efetivação continua precisando de uma transação SQL. A solução escolhida obtém a mesma garantia com o rollback nativo do banco que já existe.

### 2.2 Compensação

- **Como funcionaria aqui:** as tools continuariam com o `commit()` imediato. Ao receber o veredito de recusa, `executar_fluxo` executaria a ação inversa de cada escrita do turno: `DELETE` do `id` criado por `add_transaction`/`add_event` e, para `update_transaction`, um `UPDATE` restaurando os valores anteriores.
- **Vantagem real:** nenhuma conexão fica aberta esperando o juiz, e as tools não precisam saber de nada; é o padrão que funciona até com efeitos fora do banco.
- **Custo real:**
  - para desfazer um `UPDATE`, é preciso ter guardado a versão anterior (*before-image*), o que exige um `SELECT` a mais antes da alteração;
  - entre o commit e a compensação, o dado errado fica visível para outras sessões, inclusive o painel, outra aba e o servidor MCP;
  - se a compensação falhar (rede, processo derrubado), o dado inválido permanece e nenhuma exceção avisa;
  - um `DELETE` pode apagar uma linha que outra sessão já usou.
- **Por que ficou de fora:** com os três efeitos no mesmo Postgres, o rollback nativo dá a mesma garantia sem janela de inconsistência e sem uma segunda escrita que possa falhar.

### 2.3 Interceptação antes da tool (aprovação da tool call) — pesquisada nesta etapa

- **Como funcionaria aqui:** o LangChain oferece o `HumanInTheLoopMiddleware`, que interrompe o agente antes de uma tool configurada em `interrupt_on`. A decisão pode ser aprovar, editar ou rejeitar a chamada, e o agente retoma com `Command`. No WalSave, o "humano" seria o JEV, julgando os argumentos de `add_transaction`, `update_transaction` e `add_event` contra a fala do usuário.
- **Vantagem real:** a escrita nem começa se os argumentos não batem com o pedido. Por exemplo, a moeda trocada seria barrada antes do `INSERT`.
- **Custo real:**
  - os especialistas (`financeiro_app`, `agenda_app`) não têm checkpointer, e o middleware exige um;
  - `executar_fluxo` passaria a tratar interrupções e retomadas, o que muda o fluxo da API;
  - há uma chamada JEV a mais por tool call de escrita;
  - **o problema da prática é a frase final:** uma tool call aprovada ainda pode gerar uma frase que o juiz final recusa, e aí a escrita já teria acontecido.
- **Por que ficou de fora como mecanismo principal:** sozinha, ela não cumpre a invariante. A ideia de "interceptar" entrou de forma mais barata, no roteador JEV (seção 1.6).

### 2.4 Escrita pendente com status no próprio registro — pesquisada nesta etapa

- **Como funcionaria aqui:** `transactions` e `events` ganhariam uma coluna `status` (`pendente`/`confirmada`) e uma coluna `turno_id`. A tool gravaria com `status = 'pendente'` e daria commit; após a aprovação, um `UPDATE ... SET status = 'confirmada' WHERE turno_id = ...`; na recusa, as pendentes seriam apagadas, ou um job limparia as antigas.
- **Vantagem real:** nenhuma conexão fica aberta durante o JEV, e há rastreabilidade do que foi tentado e recusado (auditoria).
- **Custo real:**
  - é necessária uma migração de schema;
  - **todas** as consultas (`search_transactions`, `saldo_total`, `saldo_diario`, `query_events`, o MCP) precisariam filtrar `status = 'confirmada'`, o que altera o SQL de negócio das tools de leitura;
  - um `UPDATE` pendente precisaria de uma tabela de versões;
  - ainda existe uma segunda escrita (a confirmação) que pode falhar.
- **Por que ficou de fora:** viola as restrições de não alterar a lógica das tools e de preservar as consultas, e reproduz parte dos riscos da compensação.

### 2.5 Evoluções registradas, fora do escopo atual

- **Outbox transacional:** gravar, na mesma transação, um evento a ser processado depois. Serviria para levar efeitos externos, como um futuro `add_google_event`, para depois do `COMMIT` com garantia de entrega. Hoje não há efeito externo a proteger.
- **Saga orquestrada:** usar estados e compensações entre Postgres, Mongo, Qdrant e APIs. É necessária apenas se uma operação passar a atravessar vários armazenamentos de forma inseparável.

## 3. O efeito nas métricas do SRE

O monitor da Etapa 6 (`/monitor`) mede a latência do turno e o p95 (SLO de 8 s), a taxa de erro (SLO de 5%), o status `ok`/`erro`/`bloqueado`, os tokens e o custo, estes dois somando apenas as chamadas de modelo LangChain capturadas pelo `ColetorChamadas`.

**Correção necessária no registro do turno.** Antes desta etapa, `executar_fluxo` iniciava `status = 'erro'` e `rota = ''` e nunca os atualizava, então **todo turno era registrado como `erro`** e a taxa de erro do painel era 100%. Como a avaliação desta seção depende desse dado, `executar_fluxo` agora registra a `rota` do turno e o `status` calculado por `status_do_estado`, considerando apenas os rótulos deste turno (`agentes_chamados` acumula entre turnos no checkpointer). Exceções continuam deixando `status = 'erro'`.

### 3.1 Decisão tomada: transação do turno + roteador JEV

| Métrica | O que muda | Mecanismo |
|---|---|---|
| Latência / p95 | Praticamente neutra no caminho da escrita; pode cair no caminho do roteador | Uma conexão por turno no lugar de uma por tool; dois comandos curtos por tool; `COMMIT` depois do juiz, que já fazia parte do turno. O roteador troca uma chamada LLM por uma chamada JEV com timeout de 5 s. **Pior caso:** com o JEV fora do ar, o turno paga até 5 s de espera e depois o roteador LLM de contingência, o que pressiona o p95 de 8 s. Por isso o timeout ficou abaixo do SLO. |
| Conexão aberta à espera do veredito | Nova | A transação fica aberta (*idle in transaction*) durante o orquestrador, o guardrail e o juiz (timeout de 30 s). Um `INSERT` não bloqueia ninguém. Um `UPDATE` mantém o lock da linha até o `COMMIT`, e outra sessão que altere a mesma linha espera. Proteção: `idle_in_transaction_session_timeout = 120s` na conexão do turno. |
| Taxa de erro | Não aumenta com recusas | Um turno recusado termina em frase segura **sem exceção**, então conta como `ok`. Uma falha de `COMMIT` também é tratada sem exceção (rollback + frase "nada foi salvo"). **Ponto cego:** o painel não distingue "ok com escrita desfeita"; o log `[transacao]` registra o caso. Uma exceção no grafo faz rollback e é relançada, contando como `erro`, como antes. |
| Status | Passa a refletir a realidade | `ok`, `bloqueado` (guardrail de entrada) e `erro` (exceção), e não mais `erro` fixo. |
| Tokens e custo no painel | Caem | O roteador deixa de ser uma chamada LLM capturada pelo coletor (−~1.450 tokens por turno roteado pelo JEV). **Atenção:** as chamadas ao JEV (roteador e juiz) não passam pelo `ColetorChamadas`, então o painel subnotifica o custo real em ~US$ 0,00013 por turno (roteador JEV + juiz). Quando o roteador cai na contingência LLM, os tokens voltam a aparecer. |
| Rota no painel | Passa a ser registrada | `financeiro`, `agenda`, `faq` ou `fim`. |

### 3.2 Decisão não tomada: compensação

| Métrica | O que mudaria | Mecanismo |
|---|---|---|
| Latência / p95 | Aumenta nos turnos recusados | Uma conexão nova e um `DELETE`/`UPDATE` inverso por escrita recusada, depois do juiz. `update_transaction` ainda pagaria um `SELECT` antes, para guardar a versão anterior. |
| Taxa de erro | Pode esconder falhas | Se o inverso falhar e a falha for capturada para não derrubar o chat, o turno termina `ok` com o dado errado no banco. Se não for capturada, o turno vira `erro` depois de o usuário já ter visto a frase segura. |
| Segundo efeito | Novo | Cada recusa gera duas escritas (a original e a inversa), visíveis para outras sessões no intervalo entre elas. |
| Tokens e custo | Iguais | Não há chamada de modelo a mais. |

### 3.3 Decisão não tomada: Redis como preparação

| Métrica | O que mudaria | Mecanismo |
|---|---|---|
| Latência / p95 | +1 ida ao Redis por tool e +1 escrita SQL na efetivação | Com Redis gerenciado fora da região, de 20 a 80 ms por ida. |
| Taxa de erro | Novo ponto de falha | Com o Redis indisponível, as tools de escrita falham e o turno vira `erro` ou frase de falha. |
| Custo | Infraestrutura mensal | De R$ 25 a R$ 35/mês no plano Essentials de entrada; R$ 1.008/mês no mínimo do Pro. |

---

## 4. As outras decisões

### 4.1 Escopo

| Peça | Decisão | Por quê |
|---|---|---|
| `add_transaction` | **Dentro** | Escrita obrigatória; protegida pela transação do turno. |
| `update_transaction` | **Dentro** | Escrita obrigatória; a recusa restaura o registro anterior. |
| `add_event` | **Dentro** | Escrita obrigatória; protegida pela transação do turno. |
| `search_transactions`, `saldo_total`, `saldo_diario`, `query_events` | Sem mudança de código | Leituras; passam a usar a conexão do turno, o que permite enxergar a escrita pendente do próprio turno. Sem `commit()`, o savepoint delas é descartado no `close()`, que é o mesmo efeito do `close()` original. |
| `add_google_event` | **Fora** | **A tool não existe no código.** O prompt de agenda (`schedule_prompt.py`) cita `add_google_event`, mas `TOOLS_AGENDA` não a registra, então o modelo não consegue chamá-la. Se for implementada, ela tem um efeito fora do Postgres que o `ROLLBACK` não desfaz. Nesse caso, deve rodar **depois** do `COMMIT` (via Outbox, seção 2.5) ou ter compensação própria, com `DELETE` do evento no Google. |
| `salvar_mensagem` (Mongo) | **Movida** | Antes, o `guardrail_saida` gravava no Mongo a frase **ainda não julgada**, então a frase recusada ficava no histórico de sessões. Agora `executar_fluxo` grava a frase que o usuário vê, depois do juiz e da decisão de `COMMIT`/`ROLLBACK` (inclusive a frase "nada foi salvo"). Apenas os caminhos `financeiro` e `agenda` gravam aqui, porque eram os únicos que gravavam no `guardrail_saida`. O FAQ continua sem gravar a resposta, como antes. O Mongo não participa da transação: se o processo cair entre o `COMMIT` e o `salvar_mensagem`, o banco fica certo e o histórico de sessão perde essa fala. |
| Servidor MCP (`mcp_server.py`) | Sem mudança | Não passa pelo juiz e não abre turno, então `get_conn()` devolve conexão própria e o comportamento continua o de antes. |

### 4.2 O que acontece se o juiz não responde

| Situação | O que o usuário vê | O que o banco guarda |
|---|---|---|
| Sem `TYPESAFE_API_KEY` | "Não consegui confirmar essa operação com segurança, por isso nada foi salvo…" se houve escrita; senão, "Não tenho evidência para afirmar isso." | Nada do turno (`ROLLBACK`). |
| Erro HTTP do JEV | Igual ao caso anterior | `ROLLBACK`. |
| Timeout (30 s) ou resposta inválida | Igual ao caso anterior (a exceção é capturada em `no_juiz`) | `ROLLBACK`. |
| Juiz sem resposta para julgar (`juiz:sem_resposta`) | A última mensagem do grafo | `ROLLBACK` (`jev_aprovado` começa `False` em todo turno). |
| Roteador JEV sem resposta | O chat segue pelo roteador LLM original | Sem efeito; a escrita continua dependendo do juiz final. |

A regra é *fail-closed*: sem um veredito positivo explícito, nada é confirmado.

### 4.3 Se o processo cair entre dois passos

- **Entre a tool e o juiz, ou entre o juiz e o `COMMIT`:** a transação nunca foi confirmada. Quando a conexão cai, o PostgreSQL aborta a transação automaticamente e nada permanece. Se o processo travar sem fechar o socket, `idle_in_transaction_session_timeout = 120s` encerra a sessão.
- **Durante o `COMMIT`:** o PostgreSQL garante atomicidade, então ou tudo ficou ou nada ficou. O usuário pode não receber a resposta; o próximo pedido deve consultar antes de repetir.
- **Depois do `COMMIT` e antes de `salvar_mensagem`/`fechar_turno`:** o dado está correto; perdem-se a fala no Mongo e a linha do turno no monitor.

### 4.4 Repetição do mesmo pedido

- **Recusado e repetido:** a primeira tentativa não deixou nada, então a repetição aprovada grava **uma** vez.
- **Aprovado e repetido:** grava de novo, como antes da prática. O WalSave não tem chave de idempotência; dois "gastei 50 no almoço" são dois lançamentos legítimos para o sistema.
- **Reenvio HTTP do mesmo turno** (clique duplo, retry do cliente): também gera duas escritas. Resolver isso exigiria uma chave de idempotência no contrato de `/chat`, o que muda a API e ficou de fora por restrição da prática. Fica registrado como risco.

### 4.5 Evidência

**O que foi executado nesta implementação:**

1. **Teste da lógica transacional** com uma conexão falsa que reproduz a semântica de `SAVEPOINT`/`RELEASE`/`ROLLBACK TO` do PostgreSQL, usando o mesmo formato das tools (`get_conn` → `commit` no `try` → `rollback` no `except` → `close` no `finally`). Os nove casos passaram:

   | Caso | Resultado |
   |---|---|
   | Turno recusado: nada permanece | PASS |
   | Turno aprovado: duas escritas confirmadas uma única vez | PASS |
   | Erro SQL no meio: as tools seguintes continuam funcionando | PASS |
   | Erro SQL + JEV aprovado: `ROLLBACK` do turno | PASS |
   | Tool que sai sem `commit()` é desfeita, a outra fica | PASS |
   | 8 tools paralelas (threads com contexto copiado) sem corromper savepoints | PASS |
   | Turno sem tool não abre conexão | PASS |
   | Exceção no grafo: `ROLLBACK` e conexão fechada | PASS |
   | Fora do turno (MCP): conexão própria, como antes | PASS |

2. **Import e compilação do grafo real** (`app.graph.fluxo_agentes`) com os nós `guardrail_entrada`, `roteador`, `financeiro`, `agenda`, `faq`, `orquestrador`, `juiz` e `guardrail_saida`, e verificação de `jev_aprovado` em `_publicar` (aprovado → `True`; recusa → `False`).
3. **Troca da frase no estado do LangGraph** (`update_state` + `RemoveMessage`): o próximo turno recebe "nada foi salvo" no histórico, e não "registrei".

**O que ainda falta:** a execução contra o PostgreSQL real, com as tools reais e marcador único. Ela não foi executada porque o Postgres local (`localhost:5432`) estava desligado no momento da implementação. O protocolo abaixo deve ser rodado e o resultado colado nesta seção:

| # | Tool | Marcador (`source_text`/`description`) | Estado antes | Rótulo `[juiz]` | Estado depois | Esperado |
|---|---|---|---|---|---|---|
| 1 | `add_transaction` (recusa) | `[e8-add-recusa-<uuid>]` | `count = 0` | `sem_apoio …` | `count = 0` | nenhuma linha |
| 2 | `add_transaction` (aprovação) | `[e8-add-ok-<uuid>]` | `count = 0` | `apoiada noul=…` | `count = 1` | uma linha |
| 3 | `update_transaction` (recusa) | `description = [e8-upd-recusa-<uuid>]` | valor original | `sem_apoio …` | valor original | registro igual |
| 4 | `update_transaction` (aprovação) | `description = [e8-upd-ok-<uuid>]` | valor original | `apoiada noul=…` | novo valor | alterado uma vez |
| 5 | `add_event` (recusa) | `[e8-evt-recusa-<uuid>]` | `count = 0` | `sem_apoio …` | `count = 0` | nenhum evento |
| 6 | `add_event` (aprovação) | `[e8-evt-ok-<uuid>]` | `count = 0` | `apoiada noul=…` | `count = 1` | um evento |

Consultas de conferência:

```sql
SELECT count(*) FROM transactions WHERE source_text LIKE '%[e8-add-%';
SELECT id, amount, description FROM transactions WHERE id = <id_alvo>;
SELECT count(*) FROM events WHERE source_text LIKE '%[e8-evt-%';
```

Para provocar a recusa: peça um gasto em dólares (como o caso da aula) e verifique o rótulo `[juiz]` no log do uvicorn. Outra opção é rodar sem `TYPESAFE_API_KEY`, que recusa por definição (`juiz:sem_chave`).

---

## 5. Arquivos criados e arquivos modificados

### Criados

- `decisao_tecnica.md` — este relatório.

### Modificados

- `app/config/database.py` — adiciona `TurnoTransacional`, `_ConexaoDoTurno` (savepoint por tool, lock entre tools paralelas, `idle_in_transaction_session_timeout`) e `CURRENT_TURNO`; `get_conn()` devolve a conexão do turno quando há um turno ativo.
- `app/graph.py` — o estado ganha `jev_aprovado`; `no_roteador` usa o JEV, e o roteador LLM vira a contingência `_rotear_por_llm`; `salvar_mensagem` sai de `no_guardrail_saida`; `executar_fluxo` abre o turno, decide `COMMIT`/`ROLLBACK` depois do juiz, troca a frase quando a escrita foi desfeita, grava no Mongo a frase vista e registra `rota` e `status` reais no monitor.
- `app/juiz.py` — `_publicar` devolve `jev_aprovado`; adiciona `_julgar_rota`, `_historico`, os critérios de rota, as respostas diretas e `FRASE_ESCRITA_DESFEITA`; os critérios do juiz final (`REGRAS`, `LIMIAR`) não mudaram.

**Não modificados** (citados porque a versão anterior deste arquivo os listava): `app/tools/financial_tools.py` e `app/tools/calendar_tools.py`. A lógica de negócio, o SQL e os `commit()`/`rollback()` das tools permanecem idênticos.

---

## Referências

- PostgreSQL — [Transações](https://www.postgresql.org/docs/current/tutorial-transactions.html), [SAVEPOINT](https://www.postgresql.org/docs/current/sql-savepoint.html), [`idle_in_transaction_session_timeout`](https://www.postgresql.org/docs/current/runtime-config-client.html).
- Custo de subtransações e o limite de 64 por transação: [Cybertec — Subtransactions and performance in PostgreSQL](https://www.cybertec-postgresql.com/en/subtransactions-and-performance-in-postgresql), [GitLab — Why we spent the last month eliminating PostgreSQL subtransactions](https://about.gitlab.com/blog/2021/09/29/why-we-spent-the-last-month-eliminating-postgresql-subtransactions). Cada tool abre um savepoint; um turno do WalSave fica muito abaixo de 64.
- LangChain — [Human-in-the-loop middleware](https://docs.langchain.com/oss/python/langchain/human-in-the-loop).
- Redis Cloud — [Essentials plans](https://redis.io/docs/latest/operate/rc/subscriptions/view-essentials-subscription/essentials-plan-details/), [Redis Cloud now starts at $5/month](https://redis.com/blog/redis-cloud-now-starts-at-5-month/), [comparativo Upstash 2026](https://upstash.com/blog/redis-cloud-pricing-in-2026-plans-costs-and-real-examples).
- JEV — [Zuplo AI Gateway: JEV](https://zuplo.com/docs/ai-gateway/jev) (US$ 0,042 por milhão de tokens de entrada).
- Câmbio — [Banco Central: histórico de cotações](https://www.bcb.gov.br/estabilidadefinanceira/historicocotacoes); valores de outubro de 2026 em [boca.com.br](https://boca.com.br/economia/dolar/cotacao-dolar-hoje-24-10-2026) e [Rankia](https://rankia.com.br/dolar-hoje-15-outubro-2026/).
