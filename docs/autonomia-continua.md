# Autonomia contínua (MVP)

O Bauer oferece um controlador contínuo opt-in para health checks HTTP e
containers Docker. Ao montar o servidor, ele lista os containers do host e
os cadastra como alvos `docker_container` pausados (`enabled: false`), com
autocorreção habilitada por padrão. O cadastro não inicia sondagens nem
reinicia nada; cada alvo precisa ser retomado explicitamente antes de entrar
no monitoramento. A autocorreção pode ser desligada individualmente na tela
de autonomia.

Se o daemon Docker estiver indisponível, o cadastro anterior é preservado e o
servidor continua subindo normalmente. Alvos criados manualmente não são
removidos pela sincronização automática.

## Operação

- Desktop: tela **Autonomia contínua**, com iniciar, parada imediata, estado,
  última verificação, incidentes, recomendações, delegações e voz.
- Server: `GET/POST /api/autonomy/status`, `/start`, `/stop`, `/alerts`,
  `/incidents`, `/recommendations`, `/delegations` e `/delegate`; alvos
  também podem ser pausados, ter autocorreção alternada ou ser excluídos.
- CLI: `bauer autonomy continuous status|start|stop|delegate`.

O estado fica no mesmo runtime SQLite/event bus do Kernel. Para containers, a
sondagem consulta o estado do Docker e a autocorreção pode iniciar ou
reiniciar o container conforme a receita cadastrada. O monitor não executa
deploy, exclusão ou publicação. Alertas importantes podem usar o TTS
existente, mas sempre há evento/log textual como fallback.

## Delegação

`application` e `bauer_improvement` são encaminhados ao `/loop`/`bauer run`
real, com budget, aprovação, auditoria e kill-switch existentes. No server a
delegação exige Git e cria um worktree `bauer/task-*`; o branch é deixado para
revisão e uma aprovação `autonomy.merge_worktree` é registrada. O MVP não faz
merge, deploy ou publicação automaticamente.

O monitor não substitui o Kernel e não tenta controlar runs por conta própria.
Uma parada do controlador não cancela uma delegação já iniciada; para isso use
o kill-switch do runtime ou o endpoint de stop da run.
