"""Module containing helper functions for testing PyTeal Utils."""
from typing import Optional, Any
from algosdk import account, encoding, logic, mnemonic
from algosdk.future import transaction
from algosdk.atomic_transaction_composer import (
    AccountTransactionSigner,
    AtomicTransactionComposer,
)

import pyteal as pt
import beaker as bkr

client = bkr.sandbox.get_algod_client()


def returned_int_as_bytes(i: int, bits: int = 64):
    return list(i.to_bytes(bits // 8, "big"))


class TestAccount:
    def __init__(
        self,
        address: str,
        private_key: Optional[str],
        lsig: Optional[transaction.LogicSig] = None,
        app_id: Optional[int] = None,
    ):
        self.address = address
        self.private_key = private_key
        self.signer = AccountTransactionSigner(private_key)
        self.lsig = lsig
        self.app_id = app_id

        assert self.private_key or self.lsig or self.app_id

    def mnemonic(self) -> str:
        return mnemonic.from_private_key(self.private_key)

    def is_lsig(self) -> bool:
        return bool(not self.private_key and not self.app_id and self.lsig)

    def application_address(self) -> str:
        return logic.get_application_address(self.app_id)

    def sign(self, txn: transaction.Transaction):
        """Sign a transaction with an TestAccount."""
        if self.is_lsig():
            return transaction.LogicSigTransaction(txn, self.lsig)
        else:
            assert self.private_key
            return txn.sign(self.private_key)

    @classmethod
    def create(cls) -> "TestAccount":
        private_key, address = account.generate_account()
        return cls(private_key=private_key, address=str(address))

    @property
    def decoded_address(self):
        return encoding.decode_address(self.address)


accounts = [
    TestAccount(acct.address, acct.private_key) for acct in bkr.sandbox.get_accounts()
]


class UnitTestingApp(bkr.Application):

    """Base unit testable application.

    There are 2 ways to use this class


    1) Initialize with a single Expr that returns bytes
        The bytes output from the Expr are returned from the abi method `unit_test()[]byte`

    2) Subclass UnitTestingApp and override `unit_test` or any other methods with custom
        functionality.
        Any inputs or output may be specified but you're responsible for encoding the incoming
        arguments as a dict with keys matching the argument names of the custom `unit_test` method


    An instance of this class is passed to assert_output to check the return value against what you expect.
    """

    def __init__(self, expr_to_test: pt.Expr = None):
        self.expr = expr_to_test

        super().__init__()

    @bkr.create
    def create(self):
        return self.app_state.initialize()

    @bkr.delete
    def delete(self):
        return pt.Approve()

    @bkr.update
    def update(self):
        return pt.Approve()

    @bkr.opt_in
    def opt_in(self):
        return self.acct_state.initialize()

    @bkr.close_out
    def close_out(self):
        return pt.Approve()

    @bkr.external
    def opup(self):
        return pt.Approve()

    @bkr.external
    def unit_test(self, *, output: pt.abi.DynamicArray[pt.abi.Byte]):
        return pt.Seq((s := pt.abi.String()).set(self.expr), output.decode(s.encode()))


def assert_output(
    app: UnitTestingApp,
    inputs: list[dict[str, Any]],
    outputs: list[Any],
    opups: int = 0,
):
    """
    Creates and calls the UnitTestingApp passed and compares the return value with the expected output

    :param app: An instance of a UnitTestingApp to make call against its `unit_test` method
    :param inputs: A list of dicts where each entry contains keys matching the input args for the `unit_test` method  and values corresponding to the type expected by the method
    :param outputs: A list of outputs to compare against the return value of the output of the `unit_test` method
    :param opups: A number of additional app call transactions to make to increase our budget

    """
    app_client = bkr.client.ApplicationClient(client, app, signer=accounts[0].signer)
    app_client.create()

    has_state = app.acct_state.num_byte_slices + app.acct_state.num_uints > 0

    if has_state:
        app_client.opt_in()

    try:
        for idx, output in enumerate(outputs):
            input = {} if len(inputs) == 0 else inputs[idx]

            if opups > 0:
                atc = AtomicTransactionComposer()

                app_client.add_method_call(atc, app.unit_test, **input)
                for x in range(opups):
                    app_client.add_method_call(atc, app.opup, note=str(x).encode())

                try:
                    results = atc.execute(client, 2)
                except Exception as e:
                    raise app_client.wrap_approval_exception(e)

                assert results.abi_results[0].return_value == output
            else:
                result = app_client.call(app.unit_test, **input)
                assert result.return_value == output
    except Exception as e:
        raise e
    finally:
        if has_state:
            app_client.close_out()
        app_client.delete()
