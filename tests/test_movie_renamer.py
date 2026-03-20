import importlib.util
import os
import sys
import tempfile
import unittest


def load_module():
    sys.argv = ['movie_renamer.py']
    spec = importlib.util.spec_from_file_location(
        'movie_renamer',
        '/home/ben/git/jellyfin-movie-renamer/movie_renamer.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MovieRenamerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_clean_search_term_strips_release_noise(self):
        cleaned = self.mod.clean_search_term(
            'Jericho_Mansions_-_German_-_2003_-_DVDRiP_-_XviD_- (2004)'
        )
        self.assertEqual(cleaned, 'Jericho Mansions')

    def test_clean_search_term_strips_release_groups_without_dropping_title_words(self):
        self.assertEqual(
            self.mod.clean_search_term('TRON.Legacy.DVDRip.LD.German.PROPER.XViD-EPiC - Le'),
            'TRON Legacy'
        )
        self.assertEqual(
            self.mod.clean_search_term('Panik im Jahre Null (1963) DVDRiP XviD - GMA - pij'),
            'Panik im Jahre Null'
        )

    def test_expand_search_term_variants_handles_next_chapter(self):
        variants = self.mod.expand_search_term_variants(
            'Der letzte Exorzismus The next Chapter'
        )
        self.assertIn('Der letzte Exorzismus 2', variants)

    def test_generic_imdb_title_is_rejected(self):
        self.assertTrue(self.mod.is_generic_imdb_title('Besetzung und Crew-Mitglieder'))
        self.assertFalse(self.mod.is_generic_imdb_title('Der letzte Exorzismus 2'))

    def test_numeric_titles_do_not_match_wrong_number(self):
        self.assertEqual(
            self.mod.title_match_score('Ice Age 2', '2012: Ice Age'),
            0
        )
        self.assertGreater(
            self.mod.title_match_score('Ice Age 2', "Ice Age 2 - Jetzt taut's"),
            0
        )

    def test_word_order_must_stay_consistent(self):
        self.assertEqual(
            self.mod.title_match_score('mad max 2', 'Mad 2 the Max'),
            0
        )
        self.assertGreater(
            self.mod.title_match_score('mad max 2 the road warrior', 'Mad Max 2'),
            0
        )

    def test_same_tail_different_head_is_rejected(self):
        self.assertEqual(
            self.mod.title_match_score('Wolverine Weg des Kriegers', 'Anuk Der Weg des Kriegers'),
            0
        )
        self.assertGreater(
            self.mod.title_match_score('Wolverine Weg des Kriegers', 'The Wolverine'),
            0
        )
        self.assertEqual(
            self.mod.title_match_score('Reality Xl Realitaet Ist Ein Traum', 'Reality'),
            0
        )

    def test_feature_filter_rejects_games_and_podcasts(self):
        from bs4 import BeautifulSoup

        podcast_html = '''
        <div><div><div><a href="/title/tt38183794/?ref_=fn_t_5">Mad Max 2</a> Folge : Mad Max 2 ( 2021 )</div></div></div>
        '''
        game_html = '''
        <div><div><div><a href="/title/tt0795398/?ref_=fn_t_2">Ice Age 2 - Jetzt taut's</a> 2006 0 Videospiel</div></div></div>
        '''
        movie_html = '''
        <div><div><div><a href="/title/tt0438097/?ref_=fn_t_1">Ice Age 2 - Jetzt taut's</a> 2006 1h 31m</div></div></div>
        '''

        podcast_link = BeautifulSoup(podcast_html, 'html.parser').find('a')
        game_link = BeautifulSoup(game_html, 'html.parser').find('a')
        movie_link = BeautifulSoup(movie_html, 'html.parser').find('a')

        self.assertFalse(self.mod.is_feature_candidate_link(podcast_link))
        self.assertFalse(self.mod.is_feature_candidate_link(game_link))
        self.assertTrue(self.mod.is_feature_candidate_link(movie_link))

    def test_collect_metadata_title_clues_prefers_titles_over_plot_noise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            metadata_path = os.path.join(temp_dir, 'movie.xml')
            with open(metadata_path, 'w', encoding='utf-8') as handle:
                handle.write(
                    '<Title>\n'
                    '  <LocalTitle>Die Kammer der toten Kinder</LocalTitle>\n'
                    '  <OriginalTitle>La Chambre Des Morts</OriginalTitle>\n'
                    '  <Genre>Thriller</Genre>\n'
                    '  <Country>Frankreich</Country>\n'
                    '  <Description>Mitten in der Nacht überfahren zwei arbeitslose Computerprogrammierer einen Mann.</Description>\n'
                )

            clues = self.mod.collect_metadata_title_clues(temp_dir, debug=False)
            self.assertIn('Die Kammer der toten Kinder', clues)
            self.assertIn('La Chambre Des Morts', clues)
            self.assertNotIn('Thriller', clues)
            self.assertNotIn('Frankreich', clues)

    def test_collect_metadata_title_clues_ignores_xml_tags_and_desktop_ini_noise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            metadata_path = os.path.join(temp_dir, 'movie.xml')
            ini_path = os.path.join(temp_dir, 'desktop.ini.txt')
            with open(metadata_path, 'w', encoding='utf-8') as handle:
                handle.write(
                    '<LocalTitle>Reality Xl - Realitaet Ist Ein Traum</LocalTitle>\n'
                    '<MPAARating>Unrated</MPAARating>\n'
                    '<IMDB></IMDB>\n'
                )
            with open(ini_path, 'w', encoding='utf-8') as handle:
                handle.write(
                    'HotKey=0\n'
                    'IconFile=C:\\Windows\\system32\\shell32.dll\n'
                )

            clues = self.mod.collect_metadata_title_clues(temp_dir, debug=False)
            self.assertIn('Reality Xl Realitaet Ist Ein Traum', clues)
            self.assertNotIn('MPAARating Unrated MPAARating', clues)
            self.assertFalse(any('HotKey' in clue for clue in clues))
            self.assertFalse(any('IconFile' in clue for clue in clues))

    def test_collect_imdb_id_clues_from_scene_nfo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            nfo_path = os.path.join(temp_dir, 'dirtydub-wir-x264.nfo.txt')
            with open(nfo_path, 'w', encoding='utf-8') as handle:
                handle.write('www.imdb.com/title/tt6857112\n')

            imdb_ids = self.mod.collect_imdb_id_clues(temp_dir, debug=False)
            self.assertEqual(imdb_ids, ['tt6857112'])

    def test_dvd_and_iso_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_ts = os.path.join(temp_dir, 'VIDEO_TS')
            os.makedirs(video_ts, exist_ok=True)
            with open(os.path.join(video_ts, 'VIDEO_TS.IFO'), 'w', encoding='utf-8') as handle:
                handle.write('dvd marker')
            with open(os.path.join(temp_dir, 'movie.iso'), 'w', encoding='utf-8') as handle:
                handle.write('iso marker')

            self.assertTrue(self.mod.has_dvd_structure(temp_dir))
            self.assertTrue(self.mod.has_iso_image(temp_dir))

    def test_suggestion_video_items_can_be_allowed_for_metadata_id_recovery(self):
        data = {
            'd': [
                {'id': 'tt0156146', 'l': 'Tycus', 'y': 1999, 'qid': 'video'},
                {'id': 'tt0795398', 'l': "Ice Age 2 - Jetzt taut's", 'y': 2006, 'qid': 'videoGame'},
            ]
        }

        disallowed = self.mod.extract_imdb_suggestion_candidates('Tycus', data)
        allowed = self.mod.extract_imdb_suggestion_candidates('Tycus', data, allow_video=True)

        self.assertEqual(disallowed, [])
        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]['imdb_id'], 'tt0156146')

    def test_choose_preferred_local_title_keeps_clean_localized_title(self):
        candidate = {
            'title': 'Silent Running',
            'title_en': 'Silent Running',
            'year': '1972',
            'imdb_id': 'tt0067756',
        }
        chosen = self.mod.choose_preferred_local_title(
            candidate,
            ['Lautlos im Weltraum', 'False'],
            matched_search_term='Lautlos im Weltraum'
        )
        self.assertEqual(chosen, 'Lautlos im Weltraum')

    def test_detect_extra_type_from_folder_and_filename(self):
        self.assertEqual(
            self.mod.detect_extra_type('Sample', 'coincidence-life-720p-sample.mkv'),
            'sample'
        )
        self.assertEqual(
            self.mod.detect_extra_type('', 'movie-trailer.mkv'),
            'trailer'
        )
        self.assertIsNone(
            self.mod.detect_extra_type('', 'Wolverine Weg des Kriegers.avi')
        )


if __name__ == '__main__':
    unittest.main()
