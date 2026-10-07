# Ristorante Benedetto

Site responsivo em HTML, CSS e JavaScript com backend Python para checkout online, sem dependências externas.

## Executar

```sh
cd /workspace/teste1
python3 server.py
```

## Conteúdo

93 itens, 15 categorias e 40 fotos importados de https://ristoratnebenedetto.com.br/ e de seu cardápio público em 06/10/2026. Dados, preços e WhatsApp podem ser atualizados em `restaurant-config.js`. As fotos estão armazenadas em `assets/`.

## Pedidos e reservas

O cliente monta o carrinho, informa retirada ou entrega e abre uma mensagem pronta para o WhatsApp (67) 99841-8006. Reservas usam o mesmo canal. O cliente precisa enviar a mensagem, e a equipe deve confirmar disponibilidade, valores finais e atendimento. O checkout online está implementado, mas depende da configuração descrita abaixo. Reservas continuam como solicitações pelo WhatsApp. O carrinho é preservado no navegador entre visitas; dados de pedidos online são armazenados no servidor.

As fontes do Google são opcionais: fontes locais servem de fallback. Para publicar com pagamento online, use hospedagem com Python, HTTPS e armazenamento persistente privado. Esta implementação ainda não foi publicada e não altera o site original.

## Checkout online (Mercado Pago)

Execute `python3 server.py` em `/workspace/teste1` (porta padrão 8001). O servidor Python, sem dependências extras, serve o site e os endpoints de checkout. O servidor estático anterior permite somente o fluxo WhatsApp.

Configure variáveis no servidor, nunca no JavaScript público:

- `MERCADO_PAGO_ACCESS_TOKEN`: Access Token da conta do restaurante, inserido de forma segura nas configurações do ambiente.
- `PAYMENT_PUBLIC_URL`: URL HTTPS pública da nova aplicação, sem barra final. Não deve apontar ao site antigo enquanto ele não executar este backend.
- `PAYMENT_TEST_MODE=true`: checkout de teste. Só alterar para `false` depois de validar com credenciais e usuários de teste do Mercado Pago e configurar a conta de produção.
- `ORDER_DATA_DIR`: diretório privado e persistente para pedidos; padrão `.local/`. Preserve esse banco e faça backups na hospedagem.

O servidor calcula os preços pelo catálogo, guarda pedidos em SQLite, reaproveita tentativas com a mesma chave e cria uma preferência no Checkout Pro. Cartões e Pix são processados pelo Mercado Pago; o restaurante não coleta dados de cartão. A disponibilidade dos meios depende da configuração da conta. O retorno do navegador nunca aprova um pedido sozinho: o servidor consulta a API e verifica referência, moeda, valor e modo teste/produção. Uma rotina consulta até 50 pedidos recentes por minuto, durante sete dias. Não há webhook nesta versão; conciliação posterior e pedidos mais antigos precisam de acompanhamento operacional.

Pagamento online para retirada ou entrega (esta última depende da configuração do cálculo de frete). Tabela aprovada: até 3 km R$ 6,50; acima de 3 até 5 km R$ 7,50; acima de 5 até 7 km R$ 8,50; acima de 7 km R$ 10. Não foi definido limite máximo de distância.

Configure `GOOGLE_MAPS_API_KEY` no servidor, com Routes API habilitada e faturamento ativo no projeto Google Maps. Restrinja a chave à Routes API. O servidor usa Google Routes para calcular percurso de carro desde Rua Nelson de Araújo, 684, Dourados MS até o endereço completo informado. Cotações valem 15 minutos, são armazenadas no servidor e vinculadas ao endereço. Alterar o endereço exige novo cálculo. O servidor inclui o frete no valor cobrado e rejeita valores de frete enviados pelo navegador. Sem chave de mapas ou em caso de erro, entrega online fica bloqueada e WhatsApp continua disponível. A conexão real com Google Maps ainda precisa ser validada; testes de rotas são simulados.

O carrinho é mantido localmente no navegador. As informações dos pedidos online são armazenadas no banco privado; não existe painel de atendimento nesta versão. Antes de receber pedidos reais, defina com a equipe como acompanhar o banco/pedidos e confirmar preparo, além de hospedagem Python com HTTPS, backups e a validação ponta a ponta em sandbox. Hospedagem somente estática não suporta este checkout.

Validação local: `python3 -m unittest discover -s tests -v`. Os testes de gateway usam respostas simuladas e não provam acesso real à conta nem criam cobranças. A integração real ainda depende de credenciais e URL pública. `.env.example` é referência; o servidor não carrega arquivos `.env` automaticamente.


## Publicação no Render

`render.yaml` descreve um Web Service Python com plano Starter **pago** e disco persistente de 1 GB. Criar esse serviço depende de confirmar os custos no Render. Não use armazenamento temporário para pedidos reais.

Para configurar manualmente em New → Web Service:

- Repositório: Rafinhas92/teste1; branch: main.
- Runtime: Python 3.
- Build Command: `python -m compileall -q server.py`.
- Start Command: `python server.py`.
- Health Check Path: `/`.
- Variável `HOST=0.0.0.0`; `PORT` é fornecida pelo Render.
- `PAYMENT_TEST_MODE=true`.
- Disco persistente montado em `/var/data/benedetto`; `ORDER_DATA_DIR=/var/data/benedetto`.

Para uma prévia gratuita sem pagamentos, omita o disco e ORDER_DATA_DIR, e não configure credenciais. Os pedidos locais não serão persistentes nesse modo. Para aceitar pagamentos, use o serviço com armazenamento persistente.

Depois que o Render fornecer o endereço HTTPS, configure `PAYMENT_PUBLIC_URL` com esse endereço. Insira as credenciais diretamente nos campos secretos de Environment do Render. Os segredos do ambiente deste chat não são transferidos automaticamente para a hospedagem. Não publique tokens em arquivos ou no GitHub. Teste o checkout e as rotas antes de usar credenciais de produção ou conectar o domínio atual.
