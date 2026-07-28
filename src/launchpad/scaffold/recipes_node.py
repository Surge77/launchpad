"""Node stacks: Next.js full-stack and MERN."""

from __future__ import annotations

from launchpad.scaffold import files
from launchpad.scaffold.files import TaskSpec
from launchpad.scaffold.plan import Command, FileSpec, Step

NEXT_PORT = 3000
VITE_PORT = 5173
EXPRESS_PORT = 5000


def next_fullstack(name: str) -> list[Step]:
    """create-next-app, then Prisma and Auth.js v5 wired into it."""
    return [
        Command(
            label="create-next-app",
            argv=(
                "npx", "--yes", "create-next-app@latest", name,
                "--typescript", "--tailwind", "--eslint", "--app", "--src-dir",
                "--import-alias", "@/*", "--use-pnpm", "--no-git", "--yes",
            ),
            in_parent=True,
        ),
        # Pinned to Prisma 6: v7 removed `url` from the schema datasource and no
        # longer exports PrismaClient from @prisma/client, which breaks both the
        # generated schema and the Auth.js adapter wiring below.
        Command(
            label="add prisma + auth.js",
            argv=("pnpm", "add", "@prisma/client@^6", "@auth/prisma-adapter", "next-auth@beta"),
        ),
        Command(label="add prisma cli", argv=("pnpm", "add", "-D", "prisma@^6")),
        _prisma_schema(),
        _prisma_client(),
        _auth_config(),
        _auth_route(),
        # Next's type check needs a generated client; failure must not abort the
        # scaffold because the user can always rerun it.
        Command(label="generate prisma client", argv=("npx", "--yes", "prisma", "generate"), optional=True),
        files.postgres_compose(name),
        files.env_example(
            {
                "DATABASE_URL": f"postgresql://{name}:{name}@localhost:5432/{name}",
                "AUTH_SECRET": "generate-with: npx auth secret",
                "AUTH_GITHUB_ID": "",
                "AUTH_GITHUB_SECRET": "",
            }
        ),
        files.launcher_toml([TaskSpec(name="web", cmd="pnpm dev", port=NEXT_PORT, open_browser=True)]),
        files.readme(
            name,
            "Next.js 16 + Prisma + Postgres + Auth.js v5",
            [
                "`docker compose up -d` to start Postgres",
                "`cp .env.example .env` and fill in the values",
                "`npx prisma migrate dev --name init`",
                "`pnpm dev`",
            ],
            [
                "Auth.js is configured with a GitHub provider; create an OAuth app and fill AUTH_GITHUB_*.",
                "`npx auth secret` generates AUTH_SECRET.",
                "The Prisma schema already contains the Auth.js User/Account/Session models.",
            ],
        ),
    ]


def _prisma_schema() -> FileSpec:
    return FileSpec(
        path="prisma/schema.prisma",
        content="""generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id            String    @id @default(cuid())
  name          String?
  email         String?   @unique
  emailVerified DateTime?
  image         String?
  accounts      Account[]
  sessions      Session[]
}

model Account {
  userId            String
  type              String
  provider          String
  providerAccountId String
  refresh_token     String?
  access_token      String?
  expires_at        Int?
  token_type        String?
  scope             String?
  id_token          String?
  session_state     String?
  user              User    @relation(fields: [userId], references: [id], onDelete: Cascade)

  @@id([provider, providerAccountId])
}

model Session {
  sessionToken String   @unique
  userId       String
  expires      DateTime
  user         User     @relation(fields: [userId], references: [id], onDelete: Cascade)
}

model VerificationToken {
  identifier String
  token      String
  expires    DateTime

  @@id([identifier, token])
}
""",
    )


def _prisma_client() -> FileSpec:
    return FileSpec(
        path="src/lib/prisma.ts",
        content="""import { PrismaClient } from '@prisma/client';

// Next.js hot-reload would otherwise open a new pool on every edit.
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma;
""",
    )


def _auth_config() -> FileSpec:
    return FileSpec(
        path="src/auth.ts",
        content="""import NextAuth from 'next-auth';
import GitHub from 'next-auth/providers/github';
import { PrismaAdapter } from '@auth/prisma-adapter';

import { prisma } from '@/lib/prisma';

export const { handlers, auth, signIn, signOut } = NextAuth({
  adapter: PrismaAdapter(prisma),
  providers: [GitHub],
  session: { strategy: 'database' },
});
""",
    )


def _auth_route() -> FileSpec:
    return FileSpec(
        path="src/app/api/auth/[...nextauth]/route.ts",
        content="""import { handlers } from '@/auth';

export const { GET, POST } = handlers;
""",
    )


def mern(name: str) -> list[Step]:
    """Vite React client plus an Express/Mongoose API, classic client+server layout."""
    return [
        Command(
            label="create vite react client",
            argv=("npm", "create", "vite@latest", "client", "--", "--template", "react-ts"),
        ),
        Command(label="install client deps", argv=("npm", "install"), subdir="client"),
        _server_package(name),
        _server_entry(),
        _server_db(),
        _server_routes(),
        Command(
            label="install server deps",
            argv=("npm", "install", "express", "mongoose", "cors", "dotenv"),
            subdir="server",
        ),
        Command(label="install nodemon", argv=("npm", "install", "-D", "nodemon"), subdir="server"),
        files.mongo_compose(name),
        files.env_example(
            {
                "PORT": str(EXPRESS_PORT),
                "MONGODB_URI": f"mongodb://localhost:27017/{name}",
                "CLIENT_ORIGIN": f"http://localhost:{VITE_PORT}",
            }
        ),
        files.launcher_toml(
            [
                TaskSpec(name="api", cmd="npm run dev", subdir="server", port=EXPRESS_PORT),
                TaskSpec(name="web", cmd="npm run dev", subdir="client", port=VITE_PORT, open_browser=True),
            ]
        ),
        files.readme(
            name,
            "MERN — MongoDB + Express + React (Vite) + Node",
            [
                "`docker compose up -d` to start MongoDB",
                "`cp .env.example .env`",
                "`cd server && npm run dev`",
                "`cd client && npm run dev`",
            ],
            [
                "`launch up` starts the API first, waits for :5000, then the client.",
                "The API exposes GET /api/health and a sample /api/items CRUD route.",
                "Client dev server proxies nothing by default — call the API by full URL or add a Vite proxy.",
            ],
        ),
    ]


def _server_package(name: str) -> FileSpec:
    return FileSpec(
        path="server/package.json",
        content=f"""{{
  "name": "{name}-server",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "main": "src/index.js",
  "scripts": {{
    "dev": "nodemon src/index.js",
    "start": "node src/index.js"
  }}
}}
""",
    )


def _server_entry() -> FileSpec:
    return FileSpec(
        path="server/src/index.js",
        content="""import 'dotenv/config';
import cors from 'cors';
import express from 'express';

import { connectDatabase } from './db.js';
import { itemsRouter } from './routes/items.js';

const app = express();
const port = Number(process.env.PORT ?? 5000);

app.use(cors({ origin: process.env.CLIENT_ORIGIN ?? 'http://localhost:5173' }));
app.use(express.json());

app.get('/api/health', (_req, res) => res.json({ status: 'ok' }));
app.use('/api/items', itemsRouter);

app.use((err, _req, res, _next) => {
  console.error(err);
  res.status(500).json({ error: 'Internal error' });
});

try {
  await connectDatabase();
} catch (error) {
  // A raw stack trace here reads as a broken scaffold; say what to do instead.
  console.error(`\n✗ ${error.message}`);
  console.error('  1. docker compose up -d      # start MongoDB');
  console.error('  2. cp .env.example .env      # then set MONGODB_URI\n');
  process.exit(1);
}

app.listen(port, () => console.log(`api listening on :${port}`));
""",
    )


def _server_db() -> FileSpec:
    return FileSpec(
        path="server/src/db.js",
        content="""import mongoose from 'mongoose';

export async function connectDatabase() {
  const uri = process.env.MONGODB_URI;
  if (!uri) throw new Error('MONGODB_URI is not set');
  await mongoose.connect(uri);
  console.log('mongo connected');
}
""",
    )


def _server_routes() -> FileSpec:
    return FileSpec(
        path="server/src/routes/items.js",
        content="""import { Router } from 'express';
import mongoose from 'mongoose';

const itemSchema = new mongoose.Schema(
  { title: { type: String, required: true }, done: { type: Boolean, default: false } },
  { timestamps: true },
);

const Item = mongoose.model('Item', itemSchema);

export const itemsRouter = Router();

itemsRouter.get('/', async (_req, res) => {
  res.json(await Item.find().sort({ createdAt: -1 }));
});

itemsRouter.post('/', async (req, res) => {
  const { title } = req.body ?? {};
  if (typeof title !== 'string' || !title.trim()) {
    return res.status(400).json({ error: 'title is required' });
  }
  res.status(201).json(await Item.create({ title: title.trim() }));
});
""",
    )
