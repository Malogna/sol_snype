import json
import time
import base58
import requests
import functools

from art import text2art
from threading import Thread
from colorama import Fore
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from config import PRIVATE_KEY, SLIPPAGE, QUICK_BUY, QUICK_BUY_AMOUNT, TIMEOUT, RPC
from soldexpy.common.direction import Direction
from soldexpy.common.unit import Unit
from soldexpy.raydium_pool import RaydiumPool
from soldexpy.swap import Swap
from soldexpy.wallet import Wallet
from colorama import init
from solana.rpc.core import RPCException

init(autoreset=True)

debug = True


def timeout(seconds_before_timeout):
    def deco(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            res = [
                TimeoutError('function [%s] timeout [%s seconds] exceeded!' % (func.__name__, seconds_before_timeout))]

            def newFunc():
                try:
                    res[0] = func(*args, **kwargs)
                except Exception as e:
                    res[0] = e

            t = Thread(target=newFunc)
            t.daemon = True
            try:
                t.start()
                t.join(seconds_before_timeout)
            except Exception as e:
                print('error starting thread')
                raise e
            ret = res[0]
            if isinstance(ret, BaseException):
                raise ret
            return ret

        return wrapper

    return deco


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
    amm_info = {}
    all_pools = {}
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
    return float(pool.get_price(1, Direction.SPEND_BASE_TOKEN, Unit.QUOTE_TOKEN, update_vault_balance=True)[0])


def get_token_price_usd(pool):
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


# load private key
keypair = Keypair.from_bytes(base58.b58decode(PRIVATE_KEY))
# configure rpc client
client = Client(RPC)
sol_wal = Wallet(client, keypair.pubkey())

print(text2art("yosharu-sol-snype"))

print(f'Address: {keypair.pubkey()} ({sol_wal.get_sol_balance()} SOL)')

# get pool
dex_req_success = False
pool_init = False
while True:
    try:
        ask_for_pool = str(input("CA/Pool/DX: "))

        if "dexscreener.com/solana/" in ask_for_pool:
            ask_for_pool = ask_for_pool.split("solana/", 1)[1]

        if "dextools.io" in ask_for_pool:
            ask_for_pool = ask_for_pool.split("pair-explorer/", 1)[1]
            ask_for_pool = ask_for_pool.split("?t", 1)[0]

        dex_req = \
            json.loads(requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{ask_for_pool}').text)[
                'pairs'][0]
        dex_req_success = True
        ask_for_pool = dex_req['pairAddress']
        break
    except KeyboardInterrupt:
        quit()
    except Exception as e:
        try:
            dex_req = \
                json.loads(requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{ask_for_pool}').text)['pairs'][
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
    coin_liq = float(dex_req['liquidity']['usd'])

    print(f'Token name: {coin_name} (${coin_symbol}) | Price: ${coin_price} | Liquidity: ${coin_liq}')

print('Connecting to Raydium via RPC...', end='\r')
if pool_init is False:
    pool = RaydiumPool(client, ask_for_pool)
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

in_percent = False
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

time_total_start = time.time()
bought_price = 0


@timeout(TIMEOUT)
async def swap_transaction(ask_for_in_amount):
    print('Sending transaction...')
    if ask_for_action == "b":
        if ask_for_in_amount == "all":
            sol_wal_balance = sol_wal.get_sol_balance() * 0.98
            swap_txn = swap.buy(float(sol_wal_balance), SLIPPAGE, keypair)
        elif in_percent is True:
            ask_for_in_amount = sol_wal.get_sol_balance() * (float(ask_for_in_amount.replace('%', '')) / 100)
            swap_txn = swap.buy(float(ask_for_in_amount), SLIPPAGE, keypair)
        else:
            swap_txn = swap.buy(float(ask_for_in_amount), SLIPPAGE, keypair)

    elif ask_for_action == "s":
        if ask_for_in_amount == "all":
            token_wal_balance = sol_wal.get_balance(pool)[0]
            swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
        elif in_percent is True:
            token_wal_balance = sol_wal.get_balance(pool)[0] * (float(ask_for_in_amount.replace('%', '')) / 100)
            swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
        else:
            price = get_token_price_native(pool)
            ask_for_in_amount = float(ask_for_in_amount) / float(price)
            swap_txn = swap.sell(float(ask_for_in_amount), SLIPPAGE, keypair)

    time_end = time.time()
    time_spent = round(time_end - time_start, 2)
    time_spent_total = round(time_end - time_total_start, 2)

    if 'status: Ok(())' in str(swap_txn):
        if time_spent == time_spent_total:
            print(Fore.GREEN + 'Success! Transaction confirmed.' + Fore.RESET + f' ({time_spent}s)')
        else:
            print(Fore.GREEN + 'Success! Transaction confirmed.' + Fore.RESET + f' ({time_spent}s/{time_spent_total}s)')


while True:
    try:
        time_start = time.time()
        if debug is False:
            swap_transaction(ask_for_in_amount)
        bought_price = get_token_price_native(pool)
        print(f'Bought at price: {bought_price}')
        break
    except KeyboardInterrupt:
        print('Transaction cancelled, exiting... (transaction may still have gone through)')
        quit()
    except TimeoutError:
        print(f'{TIMEOUT}s passed, no transaction, trying again...')
    except RPCException as e:
        print(Fore.RED + 'RPC error, trying again...')

if bought_price != 0 and ask_for_action != 's':
    ask_for_autosell = str(input('Autosell? (y/n): '))
    try:
        if ask_for_autosell == 'y':
            # print('Sleeping for 10 seconds until token is in wallet..')
            # time.sleep(10)

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

            ask_for_autosell_method = str(input('x or mcap: '))

            if ask_for_autosell_method == 'x':
                auto_sell_x = float(input('How many X?: '))
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
                            token_wal_balance = sol_wal.get_balance(pool)[0]
                            swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
                        elif in_percent is True:
                            token_wal_balance = sol_wal.get_balance(pool)[0] * (
                                    float(ask_for_in_amount.replace('%', '')) / 100)
                            swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
                        else:
                            ask_for_in_amount = float(ask_for_in_amount) / float(price)
                            swap_txn = swap.sell(float(ask_for_in_amount), SLIPPAGE, keypair)

            elif ask_for_autosell_method == 'mcap':
                auto_sell_mcap = float(input('What mcap to sell at?: '))
                print('Watching mcap...')
                while True:
                    start_iter_time = time.time()
                    current_mcap = get_market_cap(pool, ask_for_pool)
                    print(f'Current mcap: ${current_mcap}')
                    if current_mcap >= auto_sell_mcap:
                        print('Mcap reached! Sending sell transaction...')
                        if ask_for_in_amount == "all":
                            token_wal_balance = sol_wal.get_balance(pool)[0]
                            swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
                        elif in_percent is True:
                            token_wal_balance = sol_wal.get_balance(pool)[0] * (
                                    float(ask_for_in_amount.replace('%', '')) / 100)
                            swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
                        else:
                            price = get_token_price_native(pool)
                            ask_for_in_amount = float(ask_for_in_amount) / float(price)
                            swap_txn = swap.sell(float(ask_for_in_amount), SLIPPAGE, keypair)
                    end_iter_time = time.time()
                    time.sleep(1 - (end_iter_time - start_iter_time))
    except KeyboardInterrupt:
        print('Autosell cancelled.')
        pass
