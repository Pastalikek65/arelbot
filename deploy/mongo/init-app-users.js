// Create the least-privilege application principals for the Compose stack.
//
// The image entrypoint creates the administrative root account in the
// `admin` database before it runs the files in `/docker-entrypoint-initdb.d/`,
// so the accounts below are created next to it.  The application never uses
// the root account: the bot, the worker and the development profile connect
// as `arel_app`, which may only read and write the application database.
// The test profile connects as `arel_test`, which is confined to the
// disposable test databases.
//
// The script runs only while the `mongo_data` volume is created for the first
// time.  An existing volume keeps the users it was initialised with, which
// is why `docs/OPERATIONS.md` describes creating users on the existing volume
// before enabling authentication. Never reset a production volume to add users.
const appUser = process.env.MONGO_INITDB_APP_USERNAME || "arel_app";
const appPassword = process.env.MONGO_INITDB_APP_PASSWORD;
const testUser = process.env.MONGO_INITDB_TEST_USERNAME || "arel_test";
const testPassword = process.env.MONGO_INITDB_TEST_PASSWORD;
const appDatabase = process.env.MONGO_INITDB_DATABASE || "arel_bot";
const testDatabase = "arel_bot_test";
const migrationDatabase = "arel_bot_migration_smoke";

if (!appPassword || !testPassword) {
  throw new Error("MONGO_INITDB_APP_PASSWORD and MONGO_INITDB_TEST_PASSWORD are required");
}

// Creating a user twice would abort the initialisation, so the accounts are
// updated when they already exist.  Both calls therefore converge to the
// same state.
function ensureUser(database, user, password, roles) {
  if (database.getUser(user)) {
    database.updateUser(user, { pwd: password, roles: roles });
    return;
  }
  database.createUser({ user: user, pwd: password, roles: roles });
}

ensureUser(db.getSiblingDB(appDatabase), appUser, appPassword, [
  { role: "readWrite", db: appDatabase },
]);

ensureUser(db.getSiblingDB(testDatabase), testUser, testPassword, [
  { role: "readWrite", db: testDatabase },
  // The release gate runs the migration smoke against a disposable database
  // that it creates and drops again, which needs the owner role.
  { role: "dbOwner", db: migrationDatabase },
]);
