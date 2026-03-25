const { Pool } = require('pg');

// THIS is your real connection string now
const connectionString = 'postgresql://neondb_owner:npg_4RNuaVfXGv8U@ep-square-night-abbsjsa1-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require';

console.log("---------------------------------------------------");
console.log("Attempting to connect to:", connectionString); 
console.log("---------------------------------------------------");

const pool = new Pool({
    connectionString: connectionString,
    ssl: {
        rejectUnauthorized: false
    }
});

module.exports = {
    query: (text, params) => pool.query(text, params),
};