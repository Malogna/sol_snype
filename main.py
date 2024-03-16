import json
import sys
import time

import base58
import requests
from art import text2art
from colorama import Fore
from solana.rpc.api import Client
from solders.keypair import Keypair

from config import PRIVATE_KEY, SLIPPAGE, QUICK_BUY, QUICK_BUY_AMOUNT
from soldexpy.raydium_pool import RaydiumPool
from soldexpy.swap import Swap
from soldexpy.wallet import Wallet

from colorama import init

import errno
import os
import signal
import functools

init(autoreset=True)


class TimeoutError(Exception):
    pass


def timeout(seconds=10, error_message=os.strerror(errno.ETIME)):
    def decorator(func):
        def _handle_timeout(signum, frame):
            raise TimeoutError(error_message)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            signal.signal(signal.SIGALRM, _handle_timeout)
            signal.alarm(seconds)
            try:
                result = func(*args, **kwargs)
            finally:
                signal.alarm(0)
            return result

        return wrapper

    return decorator


# load private key
keypair = Keypair.from_bytes(base58.b58decode(PRIVATE_KEY))
# configure rpc client
client = Client("https://api.mainnet-beta.solana.com")
sol_wal = Wallet(client, keypair.pubkey())

print(text2art("yosharu-sol-snype"))
print('SOL balance:', sol_wal.get_sol_balance(), 'SOL')

# get pool
while True:
    try:
        ask_for_pool = str(input("CA/Pool/DX: "))

        if "dexscreener.com/solana/" in ask_for_pool:
            ask_for_pool = ask_for_pool.split("solana/", 1)[1]

        dex_req = \
            json.loads(requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{ask_for_pool}').text)['pairs'][0]
        break
    except KeyboardInterrupt:
        quit()
    except Exception as e:
        try:
            dex_req = \
                json.loads(requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{ask_for_pool}').text)[
                    'pairs'][0]
            break
        except Exception as e:
            print(Fore.RED + 'Invalid CA/Pool/DX! (double check address)')

pool_address = dex_req['pairAddress']
coin_name = dex_req['baseToken']['name']
coin_symbol = dex_req['baseToken']['symbol']

print(f'Token name: {coin_name} (${coin_symbol})')

print('Connecting to Raydium via RPC...', end='\r')
pool = RaydiumPool(client, pool_address)
# initialize Swap
swap = Swap(client, pool)
print('Connected to Raydium!           ')

try:
    print(f'${coin_symbol} balance: {sol_wal.get_balance(pool)[0]}')
except TypeError:
    pass

if QUICK_BUY is False:
    ask_for_action = str(input("Action (b or s): "))
    ask_for_in_amount = str(input("Amount ($SOL) (number or all): "))
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

print('Sending transaction...')
time_start = time.time()

try:
    if ask_for_action == "b":
        if ask_for_in_amount == "all":
            sol_wal_balance = sol_wal.get_sol_balance() * 0.95
            swap_txn = swap.buy(sol_wal_balance, SLIPPAGE, keypair)
        else:
            swap_txn = swap.buy(float(ask_for_in_amount), SLIPPAGE, keypair)

    if ask_for_action == "s":
        if ask_for_in_amount == "all":
            token_wal_balance = sol_wal.get_balance(pool)[0]
            swap_txn = swap.sell(token_wal_balance, SLIPPAGE, keypair)
        else:
            swap_txn = swap.sell(float(ask_for_in_amount), SLIPPAGE, keypair)

    time_end = time.time()
    time_spent = round(time_end - time_start, 2)

    if 'status: Ok(())' in str(swap_txn):
        print(Fore.GREEN + 'Success! Transaction confirmed.' + Fore.RESET + f' ({time_spent}s)')
except Exception:
    type, value, traceback = sys.exc_info()
    if 'insufficient' in str(value):
        print(Fore.RED + 'Transaction failed! Wallet has insufficient funds for this transaction.')
    else:
        print(Fore.RED + 'Transaction failed! Unknown error. Traceback:')
        print(value)
