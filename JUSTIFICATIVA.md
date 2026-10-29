## Perguntas e Respostas

1.	Quais arquivos você criou ou modificou? Indique o caminho de cada um.
Modificados:
- app/config/agents.py
- app/config/database.py
- app/main.py
- app/schemas.py

Criados:
- app/routes/profile_route.py
- app/tools/profile_tools.py
- app/profile.py

2.	Por onde o perfil entra, e onde cada parte dele é gravada?
Entra através da rota /perfil vinda do front-end conectado com a rota, e dentro do arquivo profile.py está o método que é acionado na rota, salvando todos os dados do perfil no MongoDB e as restrições de forma indexada no QDrant.

3.	Como o texto livre é indexado e consultado, e por que não é busca por palavra?
É armazenado uma linha sendo um registro e é consultado da mesma forma, comparando linha a linha através da semântica. Não indexamos por palavra para compararmos contexto da forma correta.

4.	Você criou uma tool ou duas? Por quê?
Apenas uma tool para consulta, pois o cadastro é feito através de um método acionado pela rota, e não pelo agente. O agente apenas consulta os dados.

## 5. O que garante que o perfil de um usuário não apareceria para outro?

O `user_id` vem do contexto da requisição, não é preenchido pelo modelo, e filtra tanto MongoDB quanto Qdrant.
A collection vetorial usa índice tenant `keyword` para `user_id`, e o MongoDB usa índice único para o perfil.

## 6. A sua tool consulta o banco diretamente ou faz uma chamada HTTP na própria API? Por quê?

A tool consulta diretamente MongoDB e Qdrant, pois está no mesmo backend e não precisa fazer chamada HTTP interna.
Isso reduz latência e duplicação de validações, mantendo a rota HTTP como único ponto de escrita.

## 7. Por que optamos por não criar um agente “perfil”?

O perfil é contexto de apoio às recomendações financeiras, e não um domínio conversacional independente.
Por isso, a tool foi conectada ao especialista financeiro existente, sem criar outro agente.

## 8. Qual a vantagem do chat não alterar o cadastro?

A tela de perfil é o único ponto de alteração, evitando mudanças acidentais causadas por frases ambíguas no chat.
O chat apenas consulta o cadastro e direciona pedidos de mudança para a tela de perfil.
