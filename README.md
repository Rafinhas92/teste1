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

As fontes do Google são opcionais: fontes locais servem de fallback. Para publicar com pagamento online, use hospedagem com Python, HTTPS e armazenamento persistente privado. Versão de teste publicada em https://teste1-vryg.onrender.com/; o domínio original não foi alterado.

## Checkout online (Mercado Pago)

Execute `python3 server.py` em `/workspace/teste1` (porta padrão 8001). O servidor Python, sem dependências extras, serve o site e os endpoints de checkout. O servidor estático anterior permite somente o fluxo WhatsApp.

Configure variáveis no servidor, nunca no JavaScript público:

- `MERCADO_PAGO_ACCESS_TOKEN`: Em modo de teste, credencial APP_USR da aplicação vinculada ao vendedor de teste. Para produção, credencial da conta real do restaurante. Insira de forma segura nas configurações do ambiente.
- `PAYMENT_PUBLIC_URL`: URL HTTPS pública da nova aplicação, sem barra final. Não deve apontar ao site antigo enquanto ele não executar este backend.
- `PAYMENT_TEST_MODE=true`: checkout de teste. Só alterar para `false` depois de validar com credenciais e usuários de teste do Mercado Pago e configurar a conta de produção.
- `ORDER_DATA_DIR`: diretório privado e persistente para pedidos; padrão `.local/`. Preserve esse banco e faça backups na hospedagem.

O servidor calcula os preços pelo catálogo, guarda pedidos em SQLite, reaproveita tentativas com a mesma chave e cria uma preferência no Checkout Pro. Cartões e Pix são processados pelo Mercado Pago; o restaurante não coleta dados de cartão. A disponibilidade dos meios depende da configuração da conta. O retorno do navegador nunca aprova um pedido sozinho: o servidor consulta a API e verifica referência, moeda, valor e modo teste/produção. Uma rotina consulta até 50 pedidos recentes por minuto, durante sete dias. Não há webhook nesta versão; conciliação posterior e pedidos mais antigos precisam de acompanhamento operacional.

Pagamento online para retirada ou entrega (esta última depende da configuração do cálculo de frete). Tabela aprovada: até 3 km R$ 6,50; acima de 3 até 5 km R$ 7,50; acima de 5 até 7 km R$ 8,50; acima de 7 km R$ 10. Não foi definido limite máximo de distância.

Configure `ORS_API_KEY` no servidor com uma chave do OpenRouteService que permita Geocoding e Directions. O plano gratuito tem limites; consulte os limites e condições vigentes no painel. A chave é enviada em Authorization somente do servidor para api.openrouteservice.org. O backend localiza rua e número no Brasil e recusa resultados imprecisos ou ambíguos. A cobertura de endereços em Dourados precisa ser testada com a chave real. O percurso é calculado de carro desde Rua Nelson de Araújo, 684, Dourados MS. A localização do restaurante é reutilizada enquanto o processo estiver ativo. Cotações valem 15 minutos, ficam vinculadas ao endereço e incluem o frete no valor cobrado. Mudanças de endereço exigem novo cálculo. Em falha, ausência de chave ou limite atingido, entrega online fica bloqueada e WhatsApp permanece disponível. GOOGLE_MAPS_API_KEY não é mais usada. No Render, adicione ORS_API_KEY em Environment e publique a versão atual. A conexão real com ORS ainda requer credencial; testes usam respostas simuladas.

O carrinho é mantido localmente no navegador. As informações dos pedidos online são armazenadas no banco privado; o painel de atendimento está disponível em /admin. Antes de receber pedidos reais, defina com a equipe como acompanhar o banco/pedidos e confirmar preparo, além de hospedagem Python com HTTPS, backups e a validação ponta a ponta em sandbox. Hospedagem somente estática não suporta este checkout.

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

No modo de teste, o backend consulta `/users/me` e exige a marca `test_user` no vendedor antes de iniciar pagamentos. Checkout Pro abre `init_point`, inclusive para contas de teste, sem usar `sandbox_init_point`. Links de sandbox salvos em tentativas anteriores são substituídos ao tentar novamente. Mantenha PAYMENT_TEST_MODE=true e comprador e vendedor de teste distintos.

Na confirmação em teste, o backend verifica vendedor marcado test_user e collector_id da transação, além de referência, BRL e total. O campo live_mode isolado não determina uma conta de teste usando APP_USR no Checkout Pro padrão. Em produção, pagamentos sandbox não são aceitos.

Quando o ORS não localizar o restaurante com precisão, configure RESTAURANT_LATITUDE e RESTAURANT_LONGITUDE com as coordenadas verificadas do ponto de partida. Use graus decimais com ponto; não estime os valores. A ordem enviada à API é longitude, latitude. Os destinos continuam sujeitos à localização precisa por endereço.

Clientes podem marcar o ponto de entrega no mapa ou solicitar a localização do navegador. O ponto precisa ser confirmado como correspondente ao endereço; o frete usa a rota ORS para esse ponto, sem geocodificação do destino. A cotação é vinculada ao endereço e às coordenadas. Alterações exigem novo cálculo. Coordenadas são incluídas no pedido para conferência no atendimento. Mapa Leaflet 1.9.4, distribuído localmente com licença e pacote verificado por SHA-512 do registro npm; tiles públicos OpenStreetMap, sujeitos à política de uso e disponibilidade. Não há garantia de serviço ilimitado ou cobertura universal.


## Painel de atendimento

Acesse `/admin`. Configure `ADMIN_PASSWORD` diretamente em Environment do Render com uma senha exclusiva de 12 a 256 caracteres. Não coloque a senha no GitHub nem envie pelo chat. Ausência de senha ou menos de 12 caracteres mantém o login desativado. A senha é verificada com scrypt; o navegador recebe somente cookie de sessão HttpOnly e SameSite=Strict, com Secure no HTTPS. Sessões expiram em oito horas; sair ou trocar a senha invalida acessos. Cinco falhas bloqueiam novas tentativas por cinco minutos por endereço de conexão. Atrás de proxy, o bloqueio pode afetar atendentes que compartilham a mesma conexão do proxy.

O painel lista somente pedidos do checkout online, com cliente, telefone, itens, observações, endereço/ponto de entrega, total e status de pagamento separado do atendimento. Atualiza a lista a cada 30 segundos e permite consultar o Mercado Pago manualmente. Pedidos sem pagamento aprovado não avançam para preparo. Fluxos: recebido → em preparo → pronto para retirada / saiu para entrega → concluído. Cancelar atendimento não estorna pagamentos; o estorno deve ser realizado no Mercado Pago. Mudanças ficam em order_events, com horário e status anterior/novo. A senha é compartilhada da equipe, sem contas individuais nesta versão.

A API exige sessão para ler pedidos e sessão, origem e token CSRF para mudar status. Rotação da senha invalida sessões existentes. Atualizações concorrentes são rejeitadas para evitar sobrescrever outro atendente. A migração adiciona colunas ao banco existente sem excluir pedidos. No Render gratuito, o painel usa o mesmo armazenamento temporário: não resolve a perda de pedidos em reinícios/deploys. Use somente testes até configurar disco persistente. Pedidos WhatsApp e reservas continuam no WhatsApp e não entram automaticamente nesta lista.

O ambiente do pagamento é registrado por pedido. Pedidos de teste não avançam em produção; registros antigos sem ambiente confirmado exigem consultar o pagamento antes de preparar.
