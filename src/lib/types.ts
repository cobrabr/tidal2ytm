import authStatusContract from "../../contracts/auth_status.json";

/**
 * `contracts/auth_status.json` is the one place the cross-language shape of an
 * `auth.status` answer is written down, and the other two suites assert against
 * it (`sidecar/tests/test_protocol.py`, `crates/tidal2ytm-ipc/tests/round_trip.rs`).
 * The keys below are derived from it rather than restated, so a rename in the
 * contract is a compile error here instead of a screen that quietly reports
 * "not signed in" forever — the third copy of a shape, asserted by nothing, is
 * exactly what those two comments warn about.
 */
type ContractData = (typeof authStatusContract)["data"];

/**
 * The contract records one signed-out sample, so a nullable field arrives as the
 * `null` literal. The contract fixes the key and the JSON type; the wire's domain
 * for an absent value is stated here, because the sample cannot say what a
 * present one may hold. Only this contract's two fields reach this mapping.
 */
type Widen<T> = T extends string ? string : T extends null ? string | null : T;

/** The `data` half of an `auth.status` answer: one entry per provider. */
export type AuthStatus = {
	[Provider in keyof ContractData]: {
		[Field in keyof ContractData[Provider]]: Widen<ContractData[Provider][Field]>;
	};
};

/** What every provider in the payload has in common. */
export type ProviderState = AuthStatus[keyof AuthStatus];
