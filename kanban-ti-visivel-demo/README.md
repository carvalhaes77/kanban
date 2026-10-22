# Kanban TI Visível — Demonstração

Projeto demo de portfólio.

Quadro fictício. A proposta é tirar a TI da caixa-preta: a empresa toda enxerga a fila, o priorizado, a execução, o que espera fornecedor e o concluído.

## O que é de cada modo

| Recurso | Individual | Compartilhado |
|---|---|---|
| Quadro Kanban + WIP | sim | sim (só com serviço ligado) |
| Parâmetros | sim (neste navegador) | só host |
| Alarme / Timer | sim | não (não impacta outros) |
| Relatório 5 linhas | sim | sim |
| Modo TV | sim | sim |
| SLA em Aguardando | sim | sim |
| Histórico do card | sim (neste navegador) | sim (no host) |
| Descartar → Arquivados | sim (lista local) | sim (lista da sala; todos veem) |
| Excluir / limpar arquivados | sim (você) | só host |
| Backup automático | — | host (pasta `compartilhado/backup`) |
| Exportar / importar / apagar quadro compartilhado | — | só host |

**Compartilhado fica OFF por padrão.** É um **quadro apartado** do Individual (dados e ações separados). Host ou convidado: todo mundo se identifica com nome para interagir; host é só o papel ID 0 (exportar / voltar ao padrão / etc.).

## Conceito Kanban

1. **Backlog / Fila**
2. **Priorizado** — WIP máx. 3 (contador vermelho no limite ou acima)
3. **Em execução** — WIP máx. 2
4. **Aguardando** — fornecedor/outro setor + SLA em horas (padrão 24h)
5. **Concluído**

Priorizar é puxar. Card novo entra na fila.

## Como baixar e rodar (portátil)

1. Baixe o ZIP do GitHub ou clone o repositório (qualquer pasta).
2. **Individual:** abra `kanban-ti-visivel-demo/index.html` — sem instalar nada (Windows, Mac ou Linux no navegador).
3. **Uso completo / compartilhado (leigo):**
   - **Windows:** dois cliques em `subir-kanban.cmd` (atalho **Subir Kanban**).
   - **Mac:** dois cliques em `Subir-Kanban.command` (na primeira vez o macOS pode pedir permissão; se bloquear, clique com o direito → Abrir).
4. Se faltar Python, o atalho prepara no **perfil do usuário** (sem admin) e abre o quadro. Depois: **Parâmetros → Gerar chave e ativar**.
5. Não precisa de `pip`, banco, Node nem JDK. Reservas manuais: `instalar-python.cmd` (Windows) / `instalar-python-mac.sh` (Mac).

### Windows e Mac (mesmo critério)

| | Windows | Mac |
|---|---|---|
| Individual | abrir o HTML | abrir o HTML |
| Um atalho só | `subir-kanban.cmd` | `Subir-Kanban.command` |
| Parar | `parar-kanban.cmd` | `Parar-Kanban.command` |
| Python | checa → se falta, instala no usuário (sem admin) | checa `python3` → se falta, tenta Homebrew (sem sudo) |
| Onde fica o Python | `%LOCALAPPDATA%` (fora do projeto) | Homebrew / perfil (fora do projeto) |
| Temp | só download do instalador | idem (brew/cache; não é a pasta do Kanban) |
| Gatilho compartilhado | **Gerar chave e ativar** (detecta host Windows/Mac na API local) | o mesmo botão |
| Dados do quadro | pasta do projeto / `compartilhado/` | igual |

Os atalhos Mac ficam **em paralelo** aos `.cmd` (não substituem o Windows). O núcleo `servidor-local.py` informa `sistema: windows|mac|linux` em `/api/local/status` e ao ligar o compartilhado.

### Compartilhado (opcional)

1. No host: atalho **Subir Kanban** / **Subir-Kanban.command** (porta **8765**).
2. **Parâmetros → Gerar nova chave e ativar** (1ª vez) ou **Reativar (mesma chave)** — serviço na **8777**; SQLite/`backup` neste PC.
3. Quem ativou digita o **nome** e entra como **host ID 0**. **Copiar chave** para o departamento.
4. Convidados: URL do host → nome + chave (convidado; a chave não vira host).
5. **Desligar neste PC** = some da rede; **não apaga** o quadro. **Reativar** = volta tudo. **Gerar nova chave** = outro token (equipe precisa da chave nova), dados intactos.
6. Limpar o quadro só com **Voltar ao padrão (host)** na aba Compartilhado. Exportar/Importar/Parâmetros: só host.

Limite recomendado no compartilhado: cerca de **15 pessoas**.

## Roteiro de entrevista (~60 segundos)

1. Abrir o quadro individual.
2. Mostrar **Priorizado 3/3** vermelho e capacidade em **Em execução 0/2**.
3. Arrastar para estourar WIP (`4/3`) — o excesso fica visível.
4. Abrir **Relatório 5 linhas** e copiar o texto.
5. Entrar em **Modo TV** (parede / só leitura).
6. Duplo clique num card → histórico leve.
7. Descartar um card → Arquivados (fora do relatório); copiar de volta ao Backlog ou excluir de vez.
8. (Opcional) Gerar chave e ativar, entrar como host ID 0, segundo usuário com a mesma chave.

## Tecnologias

- HTML/CSS/JS no navegador + `localStorage` (individual; qualquer SO)
- Python padrão + SQLite (compartilhado sob demanda)
- Atalhos de host em paralelo: Windows (`.cmd`) e Mac (`.command` / `instalar-python-mac.sh`)
- Sem backend obrigatório

## Aviso

Dados **100% fictícios**. Sem dados reais de empresas anteriores.

## Autor

**Renato Carvalhaes** — homelab e conhecimento, portfólio GitHub.

## Licença

MIT. Veja [`../LICENSE`](../LICENSE).
