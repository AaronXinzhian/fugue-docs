let nextId = 1;
const users = new Map();

function createUser({ email, name, passwordHash }) {
  const user = { id: String(nextId++), email, name: name || '', passwordHash };
  users.set(user.id, user);
  return user;
}

function findByEmail(email) {
  for (const user of users.values()) {
    if (user.email === email) return user;
  }
  return null;
}

function findById(id) {
  return users.get(id) || null;
}

function listUsers() {
  return [...users.values()];
}

function deleteUser(id) {
  return users.delete(id);
}

module.exports = { createUser, findByEmail, findById, listUsers, deleteUser };
