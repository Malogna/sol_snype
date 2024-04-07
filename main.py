import argparse
import json
import time
import traceback
import base58
import requests

from art import text2art
from colorama import Fore
from solana.exceptions import SolanaRpcException
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from config import PRIVATE_KEY, SLIPPAGE, QUICK_BUY, QUICK_BUY_AMOUNT, TIMEOUT, RPC, DISCORD_WEBHOOK
from soldexpy.common.direction import Direction
from soldexpy.common.unit import Unit
from soldexpy.raydium_pool import RaydiumPool
from soldexpy.swap import Swap
from soldexpy.wallet import Wallet
from colorama import init
from solana.rpc.core import RPCException

init(autoreset=True)

debug = False

# Initialize parser
parser = argparse.ArgumentParser()

# Adding optional argument
parser.add_argument("-c", "--Call", help="Token to buy")
parser.add_argument("-a", "--Amount", help="Amount to buy/sell")
parser.add_argument("-x", "--X_amount", help="Xs")
parser.add_argument("-tp", "--Take_profit", help="Take profit auto sell")

# Read arguments from command line
args = parser.parse_args()

if args.Call:
    call_address = str(args.Call)

if args.Amount:
    try:
        amount_arg = float(args.Amount)
    except ValueError:
        amount_arg = str(args.Amount)

if args.X_amount:
    call_autosell_x = float(args.X_amount)

if args.Take_profit:
    take_profit_mode = True
    mcap_take_profit = float(args.Take_profit)
else:
    take_profit_mode = False

if args.Call and args.Amount and args.X_amount:
    call_mode = True
else:
    call_mode = False


def extract_pool_info(pools_list: list, mint: str) -> dict:
    for pool in pools_list:

        if pool['baseMint'].lower() == mint.lower() and pool[
            'quoteMint'] == 'So11111111111111111111111111111111111111112':
            return pool
        elif pool['quoteMint'].lower() == mint.lower() and pool[
            'baseMint'] == 'So11111111111111111111111111111111111111112':
            return pool
    raise Exception(f'{mint} pool not found!')


def fetch_pool_keys(mint: str):
    try:
        # Using this, so it will be faster else no option, we go the slower way.
        with open('all_pools.json', 'r') as file:
            all_pools = json.load(file)
        amm_info = extract_pool_info(all_pools, mint)
    except:
        resp = requests.get('https://api.raydium.io/v2/sdk/liquidity/mainnet.json', stream=True)
        pools = resp.json()
        official = pools['official']
        unofficial = pools['unOfficial']
        all_pools = official + unofficial

        # Store all_pools in a JSON file
        with open('all_pools.json', 'w') as file:
            json.dump(all_pools, file)
        try:
            amm_info = extract_pool_info(all_pools, mint)
        except:
            return "failed"

    info = {
        'amm_id': Pubkey.from_string(amm_info['id']),
        'authority': Pubkey.from_string(amm_info['authority']),
        'base_mint': Pubkey.from_string(amm_info['baseMint']),
        'base_decimals': amm_info['baseDecimals'],
        'quote_mint': Pubkey.from_string(amm_info['quoteMint']),
        'quote_decimals': amm_info['quoteDecimals'],
        'lp_mint': Pubkey.from_string(amm_info['lpMint']),
        'open_orders': Pubkey.from_string(amm_info['openOrders']),
        'target_orders': Pubkey.from_string(amm_info['targetOrders']),
        'base_vault': Pubkey.from_string(amm_info['baseVault']),
        'quote_vault': Pubkey.from_string(amm_info['quoteVault']),
        'market_id': Pubkey.from_string(amm_info['marketId']),
        'market_base_vault': Pubkey.from_string(amm_info['marketBaseVault']),
        'market_quote_vault': Pubkey.from_string(amm_info['marketQuoteVault']),
        'market_authority': Pubkey.from_string(amm_info['marketAuthority']),
        'bids': Pubkey.from_string(amm_info['marketBids']),
        'asks': Pubkey.from_string(amm_info['marketAsks']),
        'event_queue': Pubkey.from_string(amm_info['marketEventQueue'])
    }

    return info


def get_token_price_native(pool):
    while True:
        try:
            return float(pool.get_price(1, Direction.SPEND_BASE_TOKEN, Unit.QUOTE_TOKEN, update_vault_balance=True)[0])
        except Exception:
            time.sleep(1)


def get_token_price_usd(pool, known_price=None):
    if known_price is not None:
        native = known_price
    else:
        native = get_token_price_native(pool)
    sol_price = float(
        json.loads(requests.get('https://www.binance.com/api/v3/ticker/price?symbol=SOLUSDT').text)['price'])
    return native * sol_price


def get_token_address(pool_address):
    dex_req = \
        json.loads(requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pool_address}').text)['pairs'][0]
    return dex_req['baseToken']['address']


def get_token_supply(pool_address):
    headers = {"Content-Type": "application/json"}
    data = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "getTokenSupply",
        "params": [
            get_token_address(pool_address)
        ]
    }
    res = requests.post(RPC, headers=headers, json=data).json()
    return res['result']['value']['uiAmount']


def get_market_cap(pool, address):
    token_supply = get_token_supply(address)
    one_token_price = get_token_price_usd(pool)
    return token_supply * one_token_price


def get_sol_bal(wallet):
    while True:
        try:
            return wallet.get_sol_balance()
        except Exception:
            time.sleep(1)


# load private key
keypair = Keypair.from_bytes(base58.b58decode(PRIVATE_KEY))
# configure rpc client
client = Client(RPC)
sol_wal = Wallet(client, keypair.pubkey())

print(text2art("yosharu-sol-snype"))

print(f'Address: {keypair.pubkey()} ({get_sol_bal(sol_wal)} SOL)')

# get pool
dex_req_success = False
pool_init = False
while True:
    try:
        if call_mode is False:
            ask_for_pool = str(input("CA/Pool/DX: "))

            if "dexscreener.com/solana/" in ask_for_pool:
                ask_for_pool = ask_for_pool.split("solana/", 1)[1]
                ask_for_pool = ask_for_pool.split("?maker", 1)[0]

            if "dextools.io" in ask_for_pool:
                ask_for_pool = ask_for_pool.split("pair-explorer/", 1)[1]
                ask_for_pool = ask_for_pool.split("?t", 1)[0]

            if "photon-sol" in ask_for_pool:
                ask_for_pool = [i for i in ask_for_pool if 'photon-sol' in i][0]
                ask_for_pool = ask_for_pool.split("lp/", 1)[1]
                ask_for_pool = ask_for_pool.split("?handle", 1)[0]

            if "dexview" in ask_for_pool:
                ask_for_pool = [i for i in ask_for_pool if 'dexview.com/solana/' in i][0]
                ask_for_pool = ask_for_pool.split("solana/", 1)[1]
                ask_for_pool = ask_for_pool.split("?maker", 1)[0]

        else:
            ask_for_pool = call_address

        dex_req = \
            json.loads(
                requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{ask_for_pool}').text)[
                'pairs'][0]
        dex_req_success = True
        ask_for_pool = dex_req['pairAddress']
        break
    except KeyboardInterrupt:
        quit()
    except Exception as e:
        try:
            dex_req = \
                json.loads(requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{ask_for_pool}').text)[
                    'pairs'][
                    0]
            dex_req_success = True
            ask_for_pool = dex_req['pairAddress']
            break
        except Exception as e:
            try:
                pool = RaydiumPool(client, ask_for_pool)
                dex_req_success = False
                pool_init = True
                break
            except Exception:
                try:
                    print('Token not found on Dexscreener, getting liquidity pools from Raydium...')
                    ask_for_pool = fetch_pool_keys(ask_for_pool)['amm_id']
                    dex_req_success = False
                    break
                except Exception:
                    print(Fore.RED + 'Invalid CA/Pool/DX! (double check address)')

if dex_req_success is True:
    pool_address = dex_req['pairAddress']
    coin_name = dex_req['baseToken']['name']
    coin_symbol = dex_req['baseToken']['symbol']
    coin_price = float(dex_req['priceUsd'])
    coin_fdv = float(dex_req['fdv'])
    coin_liq = float(dex_req['liquidity']['usd'])
    coin_link = f'https://www.dexscreener.com/solana/{pool_address}?maker={keypair.pubkey()}'

    print(
        f'Token name: {coin_name} (${coin_symbol}) | Price: ${coin_price} | Liquidity: ${coin_liq} | Dexscreener: {coin_link}')

print('Connecting to Raydium via RPC...', end='\r')
if pool_init is False:
    try:
        pool = RaydiumPool(client, ask_for_pool)
    except (RPCException, SolanaRpcException):
        tb = traceback.format_exc()
        print(tb)
        print(Fore.RED + 'RPC error, trying again...')
        if call_mode is True:
            if '429 Too Many Requests' in tb:
                print('Too many requests, sleep for 2 sec and try again...')
                time.sleep(1)
# initialize Swap
swap = Swap(client, pool)
print(Fore.GREEN + 'Connected to Raydium!           ')

try:
    coin_bal = float(sol_wal.get_balance(pool)[0])
    if dex_req_success is True:
        print(f'${coin_symbol} balance: {coin_bal} (${round(coin_bal * coin_price, 2)})')
    else:
        print(f'Token balance: {coin_bal}')
except TypeError:
    pass

if call_mode is True:
    token_wal_balance = sol_wal.get_balance(pool)
    if type(token_wal_balance) is int:
        if token_wal_balance != 0:
            quit()
    try:
        if token_wal_balance[0]:
            if token_wal_balance[0] > 0:
                quit()
    except TypeError:
        pass

in_percent = False
if call_mode is False and take_profit_mode is False:
    if QUICK_BUY is False:

        while True:
            try:
                ask_for_action = str(input("Action (b or s): "))
                if (ask_for_action == 'b') or (ask_for_action == 's'):
                    break
                else:
                    raise Exception()
            except Exception:
                print(ask_for_action, 'isnt a valid side.')

        while True:
            try:
                ask_for_in_amount = str(input("Amount ($SOL) (number, percent or all): "))

                if float(ask_for_in_amount) > 0:
                    break
                else:
                    raise Exception()
            except Exception:
                try:
                    if '%' in ask_for_in_amount:
                        in_percent = True
                    else:
                        in_percent = False
                    break
                except Exception:
                    if ask_for_in_amount == 'all':
                        break
                    else:
                        print(ask_for_in_amount, 'isnt a number, percent or all')

    else:
        print('Quickbuying!')
        ask_for_action = 'b'
        ask_for_in_amount = QUICK_BUY_AMOUNT

        # Read in the file
        with open('config.py', 'r') as file:
            filedata = file.read()

        # Replace the target string
        filedata = filedata.replace('QUICK_BUY = True', 'QUICK_BUY = False')

        # Write the file out again
        with open('config.py', 'w') as file:
            file.write(filedata)
elif call_mode is False and take_profit_mode is True:
    ask_for_action = 's'
    ask_for_in_amount = amount_arg
else:
    ask_for_action = 'b'
    ask_for_in_amount = amount_arg

bought_price = 0


def swap_transaction_internal(in_amount, in_action):
    time_total_start = time.time()
    time_start = time.time()
    print('Sending transaction...')
    if in_action == "b":
        if in_amount == "all":
            sol_wal_balance = get_sol_bal(sol_wal) * 0.98
            swap_txn = swap.buy(float(sol_wal_balance), SLIPPAGE, keypair)
        elif in_percent is True:
            in_amount = get_sol_bal(sol_wal) * (float(in_amount.replace('%', '')) / 100)
            swap_txn = swap.buy(float(in_amount), SLIPPAGE, keypair)
        else:
            swap_txn = swap.buy(float(in_amount), SLIPPAGE, keypair)

    elif in_action == "s":
        try:
            if in_amount == "all":
                token_wal_balance = sol_wal.get_balance(pool)[0]
                swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
            elif in_percent is True:
                token_wal_balance = sol_wal.get_balance(pool)[0] * (float(in_amount.replace('%', '')) / 100)
                swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
            else:
                price = get_token_price_native(pool)
                in_amount = float(in_amount) / float(price)
                swap_txn = swap.sell(float(in_amount), SLIPPAGE, keypair)
        except ZeroDivisionError:
            quit()

    time_end = time.time()
    time_spent = round(time_end - time_start, 2)
    time_spent_total = round(time_end - time_total_start, 2)

    if 'status: Ok(())' in str(swap_txn):
        if time_spent == time_spent_total:
            print(Fore.GREEN + 'Success! Transaction confirmed.' + Fore.RESET + f' ({time_spent}s)')
        else:
            print(
                Fore.GREEN + 'Success! Transaction confirmed.' + Fore.RESET + f' ({time_spent}s/{time_spent_total}s)')

    bought_price = get_token_price_native(pool)
    bought_price_usd = get_token_price_usd(pool, bought_price)

    if in_action == 'b':
        print(f'Bought @ ${bought_price_usd}')
    elif in_action == 's':
        print(f'Sold @ ${bought_price_usd}')

    disc_req = None

    if call_mode is True and dex_req_success is True and in_action == 'b':
        data = {"embeds": [
            {"type": "rich", "title": "New token bought", "description": coin_name,
             "color": 0x06ed1a,
             "fields": [{"name": "Current Xs", "value": f"1x/{call_autosell_x}x", "inline": "true"},
                        {"name": "Status", "value": "Holding", "inline": "true"}],
             "url": coin_link}]}
        disc_req = json.loads(requests.post(f"{DISCORD_WEBHOOK}?wait=true", json=data).text)["id"]

    return bought_price, disc_req


def swap_transaction(in_amount, in_action):
    while True:
        try:
            bought_price, msg_id = swap_transaction_internal(in_amount, in_action)
            return bought_price, msg_id
        except (RPCException, SolanaRpcException):
            tb = traceback.format_exc()
            print(tb)
            print(Fore.RED + 'RPC error, trying again...')
            if '429 Too Many Requests' in tb:
                print('Too many requests, sleep for 2 sec and try again...')
                time.sleep(2)
            else:
                print('Waiting 10 sec and checking if TXN went through..')
                time.sleep(10)
                token_bal = sol_wal.get_balance(pool)
                if in_action == 'b':
                    if token_bal > 0:
                        break
                if in_action == 's:':
                    if token_bal == 0:
                        break
        except TimeoutError:
            print(f'{TIMEOUT}s passed, no transaction, trying again...')
            time.sleep(1)
        except KeyboardInterrupt:
            print('Transaction cancelled, exiting... (transaction may still have gone through)')
            quit()


if debug is False:
    if call_mode is True and dex_req_success is True:
        if coin_fdv > 5000000:
            quit()

    if take_profit_mode is False:
        bought_price, msg_id = swap_transaction(ask_for_in_amount, ask_for_action)

if (bought_price != 0 and ask_for_action != 's') or take_profit_mode is True:
    if (call_mode is False) and (take_profit_mode is False):
        ask_for_autosell = str(input('Autosell? (y/n): '))
    else:
        ask_for_autosell = 'y'

    try:
        if ask_for_autosell == 'y':
            if call_mode is False and take_profit_mode is False:
                while True:
                    try:
                        ask_for_in_amount = str(input("Autosell amount ($SOL) (number, percent or all): "))

                        if float(ask_for_in_amount) > 0:
                            break
                        else:
                            raise Exception()
                    except Exception:
                        try:
                            if '%' in ask_for_in_amount:
                                in_percent = True
                            else:
                                in_percent = False
                            break
                        except Exception:
                            if ask_for_in_amount == 'all':
                                break
                            else:
                                print(ask_for_in_amount, 'isnt a number, percent or all')
            elif take_profit_mode is True:
                ask_for_in_amount = amount_arg
            else:
                ask_for_in_amount = 'all'

            if call_mode is False and take_profit_mode is False:
                ask_for_autosell_method = str(input('x or mcap: '))
            elif take_profit_mode is True:
                ask_for_autosell_method = 'mcap'
            else:
                ask_for_autosell_method = 'x'

            if ask_for_autosell_method == 'x':
                if call_mode is False:
                    auto_sell_x = float(input('How many X?: '))
                else:
                    auto_sell_x = call_autosell_x

                previous_x = 1
                print('Watching price...')
                while True:
                    price = get_token_price_native(pool)
                    current_x = price / bought_price
                    print(
                        f'Current Xs: {round(current_x, 2)}x/{round(auto_sell_x, 2)}x (current native price: {round(price, 6)} SOL)',
                        end='\r')
                    if current_x >= auto_sell_x:
                        print('Xs reached! Sending sell transaction...')
                        if ask_for_in_amount == "all":
                            swap_transaction('all', 's')
                        elif in_percent is True:
                            token_wal_balance = sol_wal.get_balance(pool)[0] * (
                                    float(ask_for_in_amount.replace('%', '')) / 100)
                            swap_transaction(token_wal_balance, 's')
                        else:
                            ask_for_in_amount = float(ask_for_in_amount) / float(price)
                            swap_transaction(ask_for_in_amount, 's')

                        if dex_req_success is True and call_mode is True:
                            current_bal = get_sol_bal(sol_wal)
                            while True:
                                newest_bal = get_sol_bal(sol_wal)
                                if current_bal != newest_bal:
                                    data = {"embeds": [
                                        {"type": "rich", "title": "New token bought", "description": coin_name,
                                         "color": 0x06ed1a,
                                         "fields": [
                                             {"name": "Current Xs",
                                              "value": f"{round(current_x, 2)}x/{call_autosell_x}x",
                                              "inline": "true"},
                                             {"name": "Status", "value": "Sold", "inline": "true"},
                                             {"name": "Wallet", "value": f'{round(newest_bal, 6)} SOL',
                                              "inline": "true"}],
                                         "url": coin_link}]}
                                    patch_req = requests.patch(f'{DISCORD_WEBHOOK}/messages/{msg_id}',
                                                               json=data)
                                    break

                        break

                    if call_mode is True:
                        if current_x <= 0.5:
                            print('SL reached, selling all.')
                            swap_transaction('all', 's')
                            if dex_req_success is True and call_mode is True:
                                current_bal = get_sol_bal(sol_wal)
                                while True:
                                    newest_bal = get_sol_bal(sol_wal)
                                    if current_bal != newest_bal:
                                        data = {"embeds": [
                                            {"type": "rich", "title": "New token bought",
                                             "description": coin_name,
                                             "color": 0x06ed1a,
                                             "fields": [
                                                 {"name": "Achieved Xs",
                                                  "value": f"{round(current_x, 2)}x/{call_autosell_x}x",
                                                  "inline": "true"},
                                                 {"name": "Status", "value": "Sold", "inline": "true"},
                                                 {"name": "Wallet", "value": f'{round(newest_bal, 6)} SOL',
                                                  "inline": "true"}],
                                             "url": coin_link}]}
                                        patch_req = requests.patch(f'{DISCORD_WEBHOOK}/messages/{msg_id}',
                                                                   json=data)
                                        break
                            break

                    if dex_req_success is True and call_mode is True and previous_x != round(current_x, 2):
                        data = {"embeds": [
                            {"type": "rich", "title": "New token bought", "description": coin_name,
                             "color": 0x06ed1a,
                             "fields": [{"name": "Current Xs", "value": f"{round(current_x, 2)}x/{call_autosell_x}x",
                                         "inline": "true"},
                                        {"name": "Status", "value": "Holding", "inline": "true"}],
                             "url": coin_link}]}
                        patch_req = requests.patch(f'{DISCORD_WEBHOOK}/messages/{msg_id}', json=data)
                    previous_x = round(current_x, 2)

            elif ask_for_autosell_method == 'mcap':
                if take_profit_mode is False:
                    auto_sell_mcap = float(input('What mcap to sell at?: '))
                else:
                    auto_sell_mcap = mcap_take_profit

                print('Watching mcap...')
                while True:
                    current_mcap = get_market_cap(pool, ask_for_pool)
                    print(f'Current mcap: ${current_mcap}', end='\r')
                    if current_mcap >= auto_sell_mcap:
                        print('Mcap reached! Sending sell transaction...')
                        if ask_for_in_amount == "all":
                            swap_transaction('all', 's')
                        elif in_percent is True:
                            token_wal_balance = sol_wal.get_balance(pool)[0] * (
                                    float(ask_for_in_amount.replace('%', '')) / 100)
                            swap_transaction(token_wal_balance, 's')
                        else:
                            price = get_token_price_native(pool)
                            ask_for_in_amount = float(ask_for_in_amount) / float(price)
                            swap_transaction(ask_for_in_amount, 's')
                        break
    except KeyboardInterrupt:
        if call_mode is True:
            print('')
            print('Autosell cancelled, selling all.')
            swap_transaction('all', 's')
            if dex_req_success is True and call_mode is True:
                current_bal = get_sol_bal(sol_wal)
                while True:
                    newest_bal = get_sol_bal(sol_wal)
                    if current_bal != newest_bal:
                        data = {"embeds": [
                            {"type": "rich", "title": "New token bought", "description": coin_name,
                             "color": 0x06ed1a,
                             "fields": [
                                 {"name": "Achieved Xs", "value": f"{round(current_x, 2)}x/{call_autosell_x}x",
                                  "inline": "true"},
                                 {"name": "Status", "value": "Sold", "inline": "true"},
                                 {"name": "Wallet", "value": f'{round(newest_bal, 6)} SOL',
                                  "inline": "true"}],
                             "url": coin_link}]}
                        patch_req = requests.patch(f'{DISCORD_WEBHOOK}/messages/{msg_id}',
                                                   json=data)
                        break
            pass
        else:
            pass
