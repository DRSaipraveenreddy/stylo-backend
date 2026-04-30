# Supabase Integration Setup Guide

## Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```

## Step 2: Get Your Supabase Credentials
1. Go to https://app.supabase.com/
2. Create a new project or use existing one
3. Navigate to **Settings > API**
4. Copy:
   - **Project URL** → `SUPABASE_URL`
   - **Anon public key** → `SUPABASE_KEY`

## Step 3: Update .env File
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

## Step 4: Create Database Tables in Supabase

### Create `items` table:
```sql
CREATE TABLE items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  description TEXT,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Enable RLS (Row Level Security)
ALTER TABLE items ENABLE ROW LEVEL SECURITY;

-- Add policies
CREATE POLICY "Users can read their own items"
  ON items FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can create their own items"
  ON items FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own items"
  ON items FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own items"
  ON items FOR DELETE
  USING (auth.uid() = user_id);
```

## Step 5: Run the Application
```bash
uvicorn main.py --reload
```

The API will be available at `http://localhost:8000`

## API Endpoints

### Authentication
- `POST /auth/signup` - Register new user
- `POST /auth/login` - Login user

### Database Operations
- `POST /items` - Create item
- `GET /items/{user_id}` - Get user's items
- `PUT /items/{item_id}` - Update item
- `DELETE /items/{item_id}` - Delete item

### Health Check
- `GET /health` - Check API status

## Example Usage

### Sign Up
```bash
curl -X POST "http://localhost:8000/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "secure_password"
  }'
```

### Create Item
```bash
curl -X POST "http://localhost:8000/items" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Item",
    "description": "Item description",
    "user_id": "user-uuid-here"
  }'
```

## Features Included

✅ **Database**: PostgreSQL with CRUD operations
✅ **Authentication**: User signup/login with JWT
✅ **Real-time**: Supabase subscriptions ready (add as needed)
✅ **Storage**: Ready for file uploads via Supabase Storage API
✅ **CORS**: Enabled for frontend integration
✅ **RLS**: Row-level security policies for data protection

## Next Steps

- Connect a frontend (React, Vue, etc.) using `@supabase/supabase-js`
- Add file storage functionality to handle uploads
- Implement real-time subscriptions using Supabase's realtime features
- Add more complex business logic as needed
