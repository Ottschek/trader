-- D1 schema v9: news signals from two sources: the LLM analyst ('claude') and the free word list ('lexicon').
ALTER TABLE news_signals ADD COLUMN source TEXT NOT NULL DEFAULT 'claude';
